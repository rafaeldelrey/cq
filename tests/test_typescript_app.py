"""cq end to end on a TypeScript app: the fixture in tests/fixtures/ts_app, installed with ``npm ci``
and measured by ``cq run``, as test_cli.py does for a Python repo.

The app plants one finding for each tool: an eslint error, a type error, three unused exports, and a
vitest test that leaves part of the code uncovered. ``npm ci`` and knip need the npm registry: off the
network the module is skipped and says why, except in CI, where a skip would hide the npm side.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cq import cli

FIXTURE = Path(__file__).parent / "fixtures" / "ts_app"
TOOLS = ("eslint", "typecheck", "tests", "dependencies", "vulnerabilities")
SETTINGS = '[[targets]]\nname = "ui"\nkind = "typescript"\nroot = "."\nsource = ["src"]\ntests = true\n'


def git(repo: Path, *args: str) -> None:
    """A git command in ``repo``, as a fixed test identity."""
    cmd = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args]
    subprocess.run(cmd, cwd=repo, check=True, capture_output=True)


def registry_unreachable() -> str | None:
    """Why the npm side can't run here, or None when it can."""
    missing = [tool for tool in ("npm", "npx") if shutil.which(tool) is None]
    if missing:
        return f"{' and '.join(missing)} not installed"
    try:
        subprocess.run(["npm", "ping"], check=True, capture_output=True, timeout=30)
    # "Can't tell" from an outside command: no registry means the npm tools can't install.
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "the npm registry can't be reached (npm ping failed)"
    return None


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The fixture app as a committed git repo with its packages installed, measured once by cq run."""
    reason = registry_unreachable()
    if reason:
        if os.environ.get("CI"):
            pytest.fail(f"CI must measure the TypeScript app: {reason}")
        pytest.skip(reason)
    repo = tmp_path_factory.mktemp("ts") / "demo-ui"
    shutil.copytree(FIXTURE, repo)
    (repo / "cq.toml").write_text(SETTINGS)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "demo")
    # No install scripts: the fixture's tools run from their packages, and nothing else needs to.
    install = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
    subprocess.run(install, cwd=repo, check=True, capture_output=True, timeout=300)
    assert cli.main(["--repo", str(repo), "run", *(arg for tool in TOOLS for arg in ("--tool", tool))]) == 0
    (snap_file,) = (repo / ".cq" / "runs").glob("*/snapshot.json")
    return json.loads(snap_file.read_text())


def test_every_typescript_tool_runs_without_an_error(snapshot: dict) -> None:
    assert snapshot["errors"] == {}
    assert not snapshot["dirty"]  # node_modules, coverage and cq's own output never count as changes
    assert set(TOOLS) <= set(snapshot["targets"]["ui"])


def test_eslint_and_tsc_find_the_planted_errors(snapshot: dict) -> None:
    ui = snapshot["targets"]["ui"]
    assert (ui["eslint"]["errors"], ui["eslint"]["by_rule"]) == (1, {"@typescript-eslint/no-explicit-any": 1})
    assert ui["eslint"]["items"][0]["file"] == "src/math.ts"
    assert (ui["typecheck"]["errors"], ui["typecheck"]["by_rule"]) == (1, {"TS2322": 1})
    assert ui["typecheck"]["items"][0] | {"msg": ""} == {"file": "src/math.ts", "line": 16, "code": "TS2322", "msg": ""}


def test_vitest_counts_the_tests_and_the_coverage(snapshot: dict) -> None:
    tests = snapshot["targets"]["ui"]["tests"]
    assert tests["results"] | {"duration_s": None} == {
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "duration_s": None,
    }
    coverage = tests["coverage"]
    assert 0 < coverage["percent"] < 100  # math.ts's unused functions are never run
    assert [f["file"] for f in coverage["worst_files"]] == ["src/math.ts"]


def test_knip_finds_the_unused_exports(snapshot: dict) -> None:
    knip = snapshot["targets"]["ui"]["dependencies"]
    assert knip["by_kind"] == {"exports": 3}
    assert sorted(i["name"] for i in knip["items"]) == ["answer", "loose", "triple"]


def test_npm_audit_finds_nothing_in_an_app_with_no_runtime_dependencies(snapshot: dict) -> None:
    audit = snapshot["targets"]["ui"]["vulnerabilities"]
    assert (audit["vulnerable_packages"], audit["vulnerabilities"], audit["items"]) == (0, 0, [])
