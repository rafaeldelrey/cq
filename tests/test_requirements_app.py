"""cq on requirements.txt apps: Python targets with a requirements.txt and no pyproject.toml.

The parser and the pin comparison are tested on their own. The end-to-end test measures two apps
with ``cq run``: one with requirements files, one with no dependency file at all. It needs PyPI and
the advisory database: off the network it is skipped and says why, except in CI.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from cq import cli, requirements
from cq.collectors import freshness, vulnerabilities
from cq.config import Settings, Target
from cq.context import Context
from cq.requirements import Requirement

SETTINGS = """
[[targets]]
name = "svc"
kind = "python"
root = "apps/svc"
source = ["svc"]
tests = true

[[targets]]
name = "bare"
kind = "python"
root = "apps/bare"
source = ["bare"]
tests = true
"""
TOOLS = ("tests", "vulnerabilities", "freshness", "dependencies")


def git(repo: Path, *args: str) -> str:
    """A git command in ``repo``, as a fixed test identity."""
    cmd = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=repo, check=True, capture_output=True, text=True).stdout


def test_a_requirements_file_parses_to_names_and_exact_pins(tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("attrs===23.1.0\n-r requirements.txt\n")  # a cycle back, read once
    (tmp_path / "requirements.txt").write_text(
        "# a comment\n"
        "requests==2.31  # trailing comment\n"
        'flask[async]>=2 ; python_version > "3"\n'
        "six == 1.15.0\n"
        "-e git+https://user@example.com/org/ypkg.git@v1#egg=ypkg\n"
        "https://user@files.example.com/pkgs/foo-1.0.whl?x=1\n"
        "https://user@files.example.com\n"
        "./local/pkg/\n"
        "zpkg @ https://example.com/zpkg.tar.gz\n"
        "--index-url https://user@index.example.com\n"
        "-c constraints.txt\n"
        "wild==1.0.*\n"
        "long \\\n  ==1.0\n"
        "-r base.txt\n"
    )
    assert requirements.parse(tmp_path / "requirements.txt") == [
        Requirement("requests", "2.31"),
        Requirement("flask", None),
        Requirement("six", "1.15.0"),
        Requirement("ypkg", None),
        Requirement("foo-1.0.whl", None),
        Requirement("(url)", None),  # a URL with no path: never what precedes its `@`, where credentials go
        Requirement("pkg", None),
        Requirement("zpkg", None),
        Requirement("wild", None),
        Requirement("long", "1.0"),
        Requirement("attrs", "23.1.0"),
    ]


def test_the_dev_file_joins_requirements_txt(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("six\n")
    assert requirements.is_requirements_app(tmp_path)
    assert requirements.files(tmp_path) == [tmp_path / "requirements.txt"]
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "dev.txt").write_text("pytest\n")
    assert requirements.files(tmp_path)[1:] == [tmp_path / "requirements" / "dev.txt"]
    (tmp_path / "requirements-dev.txt").write_text("pytest\n")
    assert requirements.files(tmp_path)[1:] == [tmp_path / "requirements-dev.txt"]  # the first that exists
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert not requirements.is_requirements_app(tmp_path)  # a uv project, read from its lock


def project(latest: str, *released: str) -> dict:
    """PyPI's JSON for a package whose releases came out a year apart, in this order."""
    return {
        "info": {"version": latest},
        "releases": {v: [{"upload_time_iso_8601": f"{2020 + i}-01-01T00:00:00Z"}] for i, v in enumerate(released)},
    }


@pytest.mark.parametrize(
    ("pin", "data", "expected"),
    [
        ("2.0.0", project("2.0.0", "1.0.0", "2.0.0"), None),
        ("2.0", project("2.0.0", "1.0.0", "2.0.0"), None),  # 2.0 pins the release PyPI lists as 2.0.0
        ("3.0b1", project("2.0.0", "1.0.0", "2.0.0", "3.0b1"), None),  # a pre-release newer than the latest
        ("1.0.0", project("2.0.0", "1.0.0", "1.5.0", "2.0.0"), (True, 2.0)),
        ("1.5", project("1.9.0", "1.0.0", "1.5.0", "1.9.0"), (False, 1.0)),
        ("0.9", project("1.0.0", "1.0.0"), (True, 0.0)),  # a release PyPI doesn't date: no lag known
        ("1.9", project("2.0", "1.0", "2.0", "1.9"), (True, 0.0)),  # a backport after 2.0 is still behind it
        ("rel-b", project("rel-b", "rel-a", "rel-b"), None),  # not PEP 440: compared as text
        ("rel-a", project("rel-b", "rel-a", "rel-b"), (False, 1.0)),
    ],
)
def test_a_pin_is_outdated_only_behind_the_latest_release(
    monkeypatch: pytest.MonkeyPatch, pin: str, data: dict, expected: tuple[bool, float] | None
) -> None:
    monkeypatch.setattr(freshness, "pypi_project", lambda name: data)
    row = freshness.pinned_row("pkg", pin)
    assert (row and (row["major"], row["years"])) == expected
    if row:
        assert (row["current"], row["latest"]) == (pin, data["info"]["version"])


def test_release_dates_take_each_release_s_first_upload() -> None:
    data = {
        "releases": {
            "1.0": [{"upload_time_iso_8601": "2021-05-01T00:00:00Z"}, {"upload_time_iso_8601": "2021-01-01T00:00:00Z"}],
            "1.1": [],  # yanked files only: no date
        }
    }
    assert freshness.release_dates(data) == {"1.0": datetime.fromisoformat("2021-01-01T00:00:00+00:00")}


def pypi_unreachable() -> str | None:
    """Why PyPI can't be reached here, or None when it can."""
    try:
        urllib.request.urlopen("https://pypi.org/simple/six/", timeout=15).close()
    # "Can't tell" from an outside service: no PyPI means nothing can resolve or be compared.
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"PyPI can't be reached: {exc}"
    return None


@pytest.fixture(scope="module")
def measured(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    """Two apps measured once by cq run: one with requirements files, one with no dependency file."""
    reason = pypi_unreachable()
    if reason:
        if os.environ.get("CI"):
            pytest.fail(f"CI must measure the requirements.txt app: {reason}")
        pytest.skip(reason)
    repo = tmp_path_factory.mktemp("reqs") / "demo"
    svc, bare = repo / "apps" / "svc", repo / "apps" / "bare"
    for app in (svc, bare):
        (app / app.name).mkdir(parents=True)
        (app / "tests").mkdir()
    # requests 2.31.0 has an advisory fixed in 2.32.0; six is unpinned and unused.
    (svc / "requirements.txt").write_text("requests==2.31.0\nsix>=1\n")
    (svc / "requirements-dev.txt").write_text("-r requirements.txt\npytest==8.3.3\n")
    (svc / "svc" / "__init__.py").write_text('"""Svc."""\n\nimport requests\n\nVERSION = requests.__version__\n')
    # Not installed and not a package: only `python -m pytest` puts the app on the import path.
    (svc / "tests" / "test_svc.py").write_text("from svc import VERSION\n\n\ndef test_version():\n    assert VERSION\n")
    (bare / "bare" / "__init__.py").write_text('"""Bare."""\n\nONE = 1\n')
    (bare / "conftest.py").write_text("")
    (bare / "tests" / "test_bare.py").write_text("from bare import ONE\n\n\ndef test_one():\n    assert ONE == 1\n")
    (repo / "cq.toml").write_text(SETTINGS)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "demo")
    assert cli.main(["--repo", str(repo), "run", *(arg for tool in TOOLS for arg in ("--tool", tool))]) == 0
    (snap_file,) = (repo / ".cq" / "runs").glob("*/snapshot.json")
    return repo, json.loads(snap_file.read_text())


def test_every_collector_measures_a_requirements_app(measured: tuple[Path, dict]) -> None:
    repo, snapshot = measured
    assert set(snapshot["errors"]) == {"bare.dependencies"}  # deptry without a manifest is #58
    assert not snapshot["dirty"]
    assert git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".", ":!.cq") == ""
    svc = snapshot["targets"]["svc"]
    assert (svc["tests"]["results"]["passed"], svc["tests"]["results"]["errors"]) == (1, 0)
    assert svc["tests"]["coverage"]["percent"] == pytest.approx(100.0)
    audited = {i["package"] for i in svc["vulnerabilities"]["items"]}
    assert "requests" in audited and svc["vulnerabilities"]["packages"] > 3  # resolved: transitive ones too
    for item in svc["vulnerabilities"]["items"]:  # pip-audit lists some twice; each counts once (#69)
        ids = item["ids"].split(", ")
        assert len(ids) == len(set(ids)), item
    outdated = {i["package"]: i["current"] for i in svc["freshness"]["items"]}
    assert outdated == {"requests": "2.31.0", "pytest": "8.3.3"}  # the dev file's pins are read too
    assert (svc["freshness"]["unpinned"], svc["freshness"]["unpinned_packages"]) == (1, ["six"])
    assert [(i["code"], i["module"]) for i in svc["dependencies"]["items"]] == [("DEP002", "six")]


def test_an_app_without_a_dependency_file_says_so_and_still_runs_its_tests(measured: tuple[Path, dict]) -> None:
    _, snapshot = measured
    bare = snapshot["targets"]["bare"]
    assert bare["vulnerabilities"]["skipped"] == requirements.SKIP_NO_MANIFEST
    assert bare["freshness"]["skipped"] == requirements.SKIP_NO_MANIFEST
    assert bare["tests"]["results"]["passed"] == 1


def test_hashes_after_a_pin_keep_it_a_pin(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "requests==2.31.0 \\\n    --hash=sha256:aaaa \\\n    --hash=sha256:bbbb\nFoo_Bar.baz==1.0 ; python_version > '3'\n"
    )
    assert requirements.parse(tmp_path / "requirements.txt") == [
        Requirement("requests", "2.31.0"),
        Requirement("foo-bar-baz", "1.0"),  # PEP 503: compared as PyPI compares names
    ]


def app_context(tmp_path: Path) -> Context:
    """A Context for the Python app at ``tmp_path/app``."""
    (tmp_path / "app").mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    target = Target(name="app", kind="python", root="app", source=("app",), tests=False)
    return Context(target=target, checkout=tmp_path, raw_dir=raw, settings=Settings(targets=(target,)))


def test_a_package_pinned_in_one_file_is_not_also_unpinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = app_context(tmp_path)
    (ctx.app_dir / "requirements.txt").write_text("Requests==1.0.0\nsix\n")
    (ctx.app_dir / "requirements-dev.txt").write_text("requests>=1\nSix>=1\n")
    monkeypatch.setattr(freshness, "pypi_project", lambda name: project("2.0.0", "1.0.0", "2.0.0"))
    result = freshness.collect(ctx)
    assert [i["package"] for i in result["items"]] == ["requests"]
    assert (result["unpinned"], result["unpinned_packages"]) == (1, ["six"])


def test_a_lock_without_a_project_is_skipped_by_both_collectors(tmp_path: Path) -> None:
    ctx = app_context(tmp_path)
    (ctx.app_dir / "uv.lock").write_text("")
    assert vulnerabilities.collect(ctx) == {"skipped": requirements.SKIP_NO_MANIFEST}
    assert freshness.collect(ctx) == {"skipped": requirements.SKIP_NO_MANIFEST}


def test_npm_freshness_rows_match_the_python_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The npm side builds its rows with the same helper: an up-to-date package is left out."""
    ctx = app_context(tmp_path)
    outdated = {"left-pad": {"current": "1.0.0", "latest": "2.0.0"}, "same": {"current": "1.0.0", "latest": "1.0.0"}}
    times = {"created": "x", "1.0.0": "2020-01-01T00:00:00Z", "2.0.0": "2022-01-01T00:00:00Z"}

    def fake_npm(cmd: list[str], cwd: Path, **_: object) -> str:
        return json.dumps(outdated if cmd[1] == "outdated" else times)

    monkeypatch.setattr(freshness, "run", fake_npm)
    result = freshness.collect_typescript(ctx)
    assert result["items"] == [
        {"package": "left-pad", "current": "1.0.0", "latest": "2.0.0", "major": True, "years": 2.0}
    ]
    assert (result["outdated"], result["libyear"]) == (1, 2.0)


def test_only_the_pin_whose_marker_holds_here_is_measured(tmp_path: Path) -> None:
    here, elsewhere = (sys.platform, "no-such-platform")
    (tmp_path / "requirements.txt").write_text(
        f'pkg==2.0 ; sys_platform == "{elsewhere}"\n'
        f'pkg==1.9 ; sys_platform == "{here}" --hash=sha256:aaaa\n'
        'old==1.0 ; python_version < "3"\n'
    )
    assert requirements.parse(tmp_path / "requirements.txt") == [Requirement("pkg", "1.9")]
