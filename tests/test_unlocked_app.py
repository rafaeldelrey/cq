"""cq on uv apps without a uv.lock (#60): resolved in the run's folder with the app's own uv settings.

Each app sets `exclude-newer` (resolve as of a date) in a different place: its uv.toml, its
[tool.uv], or a parent's [tool.uv]. The resolved `six` shows which setting uv applied. The run needs
PyPI: off the network it is skipped and says why, except in CI.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cq import cli, requirements

PROJECT = '[project]\nname = "{name}"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndependencies = ["six"]\n'
SETTINGS = "".join(
    f'\n[[targets]]\nname = "{name}"\nkind = "python"\nroot = "{root}"\nsource = ["{name}"]\n'
    for name, root in (("x", "apps/x"), ("y", "apps/y"), ("z", "apps/zone/z"), ("w", "apps/w"), ("v", "apps/v"))
)
# exclude-newer per app, and the six release each date resolves to.
EXPECTED_SIX = {"x": "1.13.0", "y": "1.12.0", "z": "1.11.0"}


def git(repo: Path, *args: str) -> str:
    """A git command in ``repo``, as a fixed test identity."""
    cmd = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=repo, check=True, capture_output=True, text=True).stdout


def pypi_unreachable() -> str | None:
    """Why PyPI can't be reached here, or None when it can."""
    try:
        urllib.request.urlopen("https://pypi.org/simple/six/", timeout=15).close()
    # "Can't tell" from an outside service: no PyPI means nothing can resolve.
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"PyPI can't be reached: {exc}"
    return None


@pytest.fixture(scope="module")
def measured(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict]:
    """Four unlocked apps measured once by cq run: (repo, run folder, snapshot)."""
    reason = pypi_unreachable()
    if reason:
        if os.environ.get("CI"):
            pytest.fail(f"CI must measure the unlocked uv apps: {reason}")
        pytest.skip(reason)
    repo = tmp_path_factory.mktemp("unlocked") / "demo"
    files = {
        "cq.toml": SETTINGS,
        "apps/x/pyproject.toml": PROJECT.format(name="x"),
        "apps/x/uv.toml": 'exclude-newer = "2020-01-01T00:00:00Z"\n',
        "apps/y/pyproject.toml": PROJECT.format(name="y") + '\n[tool.uv]\nexclude-newer = "2019-01-01T00:00:00Z"\n',
        "apps/zone/pyproject.toml": '[tool.uv]\nexclude-newer = "2018-01-01T00:00:00Z"\n',
        "apps/zone/z/pyproject.toml": PROJECT.format(name="z"),
        # A path dependency (not on PyPI) and a dev group pinned to a release with an advisory.
        "apps/w/pyproject.toml": PROJECT.format(name="w").replace('["six"]', '["six", "helper"]')
        + '\n[dependency-groups]\ndev = ["pytest==8.3.3"]\n\n[tool.uv.sources]\nhelper = { path = "../../libs/helper" }\n',
        "libs/helper/pyproject.toml": PROJECT.format(name="helper").replace('["six"]', "[]")
        + '\n[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n',
        "libs/helper/helper/__init__.py": "",
        # A path dependency whose metadata only a build can give (setuptools, dynamic): never built here.
        "apps/v/pyproject.toml": PROJECT.format(name="v").replace('["six"]', '["six", "dyn"]')
        + '\n[tool.uv.sources]\ndyn = { path = "../../libs/dyn" }\n',
        "libs/dyn/pyproject.toml": '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        '\n[project]\nname = "dyn"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndynamic = ["dependencies"]\n'
        '\n[tool.setuptools.dynamic]\ndependencies = { file = ["deps.in"] }\n\n[tool.setuptools]\npackages = ["dyn"]\n',
        "libs/dyn/deps.in": "six\n",
        "libs/dyn/dyn/__init__.py": "",
    }
    for name in ("x", "y", "z", "w", "v"):
        folder = "apps/zone/z" if name == "z" else f"apps/{name}"
        files[f"{folder}/{name}/__init__.py"] = f'"""{name}."""\n\nimport six\n'
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "demo")
    assert cli.main(["--repo", str(repo), "run", "--tool", "vulnerabilities", "--tool", "freshness"]) == 0
    (snap_file,) = (repo / ".cq" / "runs").glob("*/snapshot.json")
    return repo, snap_file.parent, json.loads(snap_file.read_text())


def test_each_apps_own_uv_settings_decide_its_resolution(measured: tuple[Path, Path, dict]) -> None:
    _, run_dir, snapshot = measured
    assert snapshot["errors"] == {}
    for name, six in EXPECTED_SIX.items():
        resolved = (run_dir / name / "requirements.txt").read_text().splitlines()
        assert f"six=={six}" in resolved, (name, resolved)
        assert snapshot["targets"][name]["vulnerabilities"]["packages"] == 1
        assert snapshot["targets"][name]["freshness"]["unpinned_packages"] == ["six"]


def test_a_path_dependency_stays_out_of_the_audit_and_the_dev_group_is_in(measured: tuple[Path, Path, dict]) -> None:
    _, run_dir, snapshot = measured
    resolved = (run_dir / "w" / "requirements.txt").read_text().splitlines()
    assert "pytest==8.3.3" in resolved and not any("helper" in line for line in resolved)
    audit = snapshot["targets"]["w"]["vulnerabilities"]
    assert "pytest" in {i["package"] for i in audit["items"]}
    assert audit["unaudited"] == 1  # the path dependency, counted rather than read as clean
    fresh = snapshot["targets"]["w"]["freshness"]
    assert {i["package"] for i in fresh["items"]} == {"pytest"}
    assert fresh["unpinned_packages"] == ["six"]  # helper comes from a path, not PyPI


def test_a_path_dependency_that_needs_a_build_is_skipped_not_built(measured: tuple[Path, Path, dict]) -> None:
    _, _, snapshot = measured
    assert snapshot["targets"]["v"]["vulnerabilities"] == {
        "skipped": requirements.SKIP_LOCAL_BUILD,
        "seconds": snapshot["targets"]["v"]["vulnerabilities"]["seconds"],
    }
    assert snapshot["targets"]["v"]["freshness"]["unpinned_packages"] == ["six"]  # dyn is the repo's own


def test_the_run_writes_nothing_into_the_repo(measured: tuple[Path, Path, dict]) -> None:
    repo, _, snapshot = measured
    assert not snapshot["dirty"]
    assert git(repo, "status", "--porcelain", "--untracked-files=all", "--ignored", "--", ".", ":!.cq") == ""
    assert not list(repo.rglob("uv.lock")) and not list(repo.rglob("*.egg-info"))
