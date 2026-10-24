"""The cq command end to end, on real throwaway git repos, with the tools that need nothing installed."""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from cq import cli, complexity_gate, gate, proc, runner
from cq.backfill import commits_to_measure
from cq.collectors import freshness, vulnerabilities
from cq.config import DEFAULTS_DIR, QLTY_IGNORE_DEFAULT, Target, load_settings
from cq.context import Context
from cq.proc import ToolError
from cq.report import latest_run

PRICING = '''"""Pricing."""


def price(qty: int, unit: float, vip: bool = False) -> float:
    """Total price."""
    total = qty * unit
    if vip:
        total *= 0.9
    return total


def unused_helper(x):
    return x * 2
'''


def git(repo: Path, *args: str) -> str:
    """A git command in ``repo``, as a fixed test identity."""
    cmd = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A uv-style repo with src/, tests/ and two commits on main."""
    root = tmp_path / "acme"
    (root / "src" / "acme").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'acme'\nversion = '0.1.0'\n")
    (root / "src" / "acme" / "__init__.py").write_text('"""Acme."""\n')
    (root / "src" / "acme" / "pricing.py").write_text(PRICING)
    (root / "tests" / "test_pricing.py").write_text("def test_x() -> None:\n    assert True\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first")
    (root / "README.md").write_text("# acme\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "second")
    return root


def run_cq(repo: Path, *args: str) -> int:
    """``cq --repo <repo> …``, as the command line would run it."""
    return cli.main(["--repo", str(repo), *args])


def test_a_partial_run_writes_a_run_folder_but_no_history(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cq(repo, "run", "--tool", "size", "--tool", "complexity", "--tool", "dead_code", "--print") == 0
    settings = load_settings(repo)
    run_dir = latest_run(settings.runs_dir)
    assert run_dir is not None
    snap = json.loads((run_dir / "snapshot.json").read_text())
    assert snap["partial"] is True
    assert snap["targets"]["acme"]["size"]["files"] == 2
    assert snap["targets"]["acme"]["complexity"]["functions"] == 2
    assert "unused function 'unused_helper'" in {i["msg"] for i in snap["targets"]["acme"]["dead_code"]["items"]}
    assert snap["tool_versions"]["cq"]
    assert snap["configs"]["acme.vulture"]["file"] == "cq:vulture.toml"
    assert (run_dir / "report.md").exists() and (run_dir / "dashboard.html").exists()
    assert not settings.history_dir.exists()
    assert json.loads(capsys.readouterr().out)["run_id"] == snap["run_id"]
    assert git(repo, "status", "--porcelain") == ""  # .cq/ ignores itself


def test_report_rerenders_the_latest_run(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cq(repo, "report") == 1  # nothing measured yet
    run_cq(repo, "run", "--tool", "size")
    capsys.readouterr()
    assert run_cq(repo, "report", "--summary") == 0
    assert "acme" in capsys.readouterr().out


def test_an_unknown_target_and_a_folder_outside_git_are_refused(repo: Path, tmp_path: Path) -> None:
    assert run_cq(repo, "run", "--target", "nope") == 2
    assert run_cq(tmp_path / "not-a-repo", "run") == 2


def test_a_past_commit_is_measured_in_a_worktree_outside_the_repo(repo: Path) -> None:
    settings = load_settings(repo)
    first = git(repo, "rev-list", "--max-parents=0", "HEAD")
    with runner.checkout_at(first, settings) as checkout:
        assert not checkout.is_relative_to(repo)
        assert checkout == checkout.resolve()  # tool output names resolved paths
        assert not (checkout / "README.md").exists()
    assert not checkout.exists()
    snap = runner.snapshot(settings, rev=first, tools=["size"])
    paths = runner.save(snap, settings, to_history=True)
    assert paths[-1].parent == settings.history_dir
    assert runner.history_sha(paths[-1]) == snap["short"] == git(repo, "rev-parse", "--short=7", first)


def test_commit_info_ignores_cqs_own_output(repo: Path) -> None:
    settings = load_settings(repo)
    run_cq(repo, "run", "--tool", "size")
    assert runner.commit_info("HEAD", settings)["dirty"] is False
    (repo / "src" / "acme" / "pricing.py").write_text(PRICING + "\n")
    assert runner.commit_info("HEAD", settings)["dirty"] is True


def test_backfill_lists_and_skips_commits_already_measured(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = load_settings(repo)
    assert run_cq(repo, "backfill") == 2  # needs --since or --rev
    first = git(repo, "rev-list", "--max-parents=0", "HEAD")
    assert run_cq(repo, "backfill", "--rev", first, "--dry-run") == 0
    assert "1 commit(s) to measure" in capsys.readouterr().err
    runner.save(runner.snapshot(settings, rev=first, tools=["size"]), settings, to_history=True)
    assert commits_to_measure(settings, [first], None, 14, force=False) == []
    assert commits_to_measure(settings, [first], None, 14, force=True) == [first]
    # Each step takes main's last commit before it; this repo's commits are all newer than 2000.
    assert commits_to_measure(settings, [], "2000-01-01", 3650, force=False) == []


def test_backfill_measures_into_history(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(repo)
    first = git(repo, "rev-list", "--max-parents=0", "HEAD")
    monkeypatch.setattr("cq.backfill.snapshot", lambda s, rev: runner.snapshot(s, rev=rev, tools=["size"]))
    assert run_cq(repo, "backfill", "--rev", first) == 0
    assert [runner.history_sha(p) for p in settings.history_dir.glob("*.json")] == [first[:7]]


def test_complexity_commands_use_the_settings_baseline(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    complex_fn = (
        "def busy(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(16)) + "    return -1\n"
    )
    (repo / "src" / "acme" / "busy.py").write_text(complex_fn)
    assert run_cq(repo, "complexity", "check", "src") == 1
    assert "busy" in capsys.readouterr().out
    assert run_cq(repo, "complexity", "baseline", "src") == 0
    baseline = json.loads((repo / ".complexity-baseline.json").read_text())
    assert baseline["functions"] == {"src/acme/busy.py::busy": 17}
    assert run_cq(repo, "complexity", "check", "src") == 0
    (repo / "src" / "acme" / "busy.py").write_text("def busy(x):\n    return x\n")
    assert run_cq(repo, "complexity", "check", "src") == 1  # improved: the baseline must follow it down
    assert "lower the baseline" in capsys.readouterr().out
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "busy")
    assert run_cq(repo, "complexity", "report", "src", "--base", "HEAD~1") == 0
    assert "| `src/acme/busy.py::busy` | — | 1 (A) |" in capsys.readouterr().out


def test_complexity_check_flags_a_baseline_entry_whose_function_is_gone(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".complexity-baseline.json").write_text(json.dumps({"max": 15, "functions": {"src/acme/gone.py::f": 20}}))
    assert run_cq(repo, "complexity", "check", "src", "--max", "15") == 1
    assert "no longer exists" in capsys.readouterr().out
    assert complexity_gate.in_paths("src/acme/gone.py::f", ["src"])


def test_complexity_report_says_when_nothing_changed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cq(repo, "complexity", "report", "src", "--base", "HEAD") == 0
    assert "No function changed complexity." in capsys.readouterr().out


def test_cleanup_removes_stale_worktrees(repo: Path) -> None:
    settings = load_settings(repo)
    stale = runner.worktrees_dir(settings) / "abc1234"
    stale.mkdir(parents=True)
    runner.cleanup_worktrees(settings)
    assert not runner.worktrees_dir(settings).exists()


def test_the_app_environment_tools_run_on_a_dependency_group_repo(repo: Path) -> None:
    """ruff, pyright, deptry and complexipy on a repo whose dev tools are a dependency group (spike finding 4)."""
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + "dependencies = []\n\n[dependency-groups]\ndev = ['ruff', 'pyright']\n"
    )
    tools = ("ruff", "pyright", "dependencies", "cognitive", "duplication", "freshness")
    assert run_cq(repo, "run", *(arg for t in tools for arg in ("--tool", t))) == 0
    run_dir = latest_run(load_settings(repo).runs_dir)
    assert run_dir is not None
    snap = json.loads((run_dir / "snapshot.json").read_text())
    assert snap["errors"] == {}, snap["errors"]
    measured = snap["targets"]["acme"]
    assert measured["dependencies"]["issues"] == 0
    assert measured["cognitive"]["functions"] == 2
    assert "violations" in measured["ruff"]
    assert measured["pyright"]["errors"] >= 0
    assert measured["duplication"]["clones"] == 0
    # No uv.lock: the declared pins are what freshness reads (#60), and the dev group's are open.
    assert (measured["freshness"]["unpinned_packages"], measured["freshness"]["outdated"]) == (["pyright", "ruff"], 0)


def test_a_run_writes_no_lock_into_an_app_without_one(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The uv-based tools run isolated when the app has no uv.lock, freshness and vulnerabilities
    measure what it declares without writing a lock (#60), and pytest leaves no bytecode behind."""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)  # as on a machine that doesn't set it
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "dependencies = []\n")
    git(repo, "commit", "-qam", "deps")
    tools = ("ruff", "pyright", "dependencies", "tests", "freshness", "vulnerabilities")
    assert run_cq(repo, "run", "--no-history", *(arg for t in tools for arg in ("--tool", t))) == 0
    assert git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".", ":!.cq") == ""
    assert not (repo / "uv.lock").exists() and not (repo / ".venv").exists()
    run_dir = latest_run(load_settings(repo).runs_dir)
    assert run_dir is not None
    snap = json.loads((run_dir / "snapshot.json").read_text())
    assert snap["errors"] == {}, snap["errors"]
    assert snap["targets"]["acme"]["freshness"]["outdated"] == 0  # never off a stray lock
    assert snap["targets"]["acme"]["vulnerabilities"]["packages"] == 0  # no dependencies, none audited


def test_a_workspace_member_runs_against_the_workspace_lock(tmp_path: Path) -> None:
    """A uv workspace member has no uv.lock of its own: it's locked at the root, and stays so."""
    root = tmp_path / "ws"
    member = root / "apps" / "a"
    (member / "a").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[tool.uv.workspace]\nmembers = ['apps/*']\n")
    (member / "pyproject.toml").write_text(
        "[project]\nname = 'a'\nversion = '0.1.0'\nrequires-python = '>=3.12'\ndependencies = ['six==1.15.0']\n"
    )
    (member / "a" / "__init__.py").write_text('"""A."""\n')
    subprocess.run(["uv", "lock", "-q"], cwd=root, check=True, capture_output=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ws")
    assert proc.has_lock(member) and proc.unlocked(member) == []
    target = Target(name="a", kind="python", root="apps/a", source=("a",), tests=False)
    raw = tmp_path / "raw"
    raw.mkdir()
    ctx = Context(target=target, checkout=root, raw_dir=raw, settings=load_settings(root))
    result = freshness.collect(ctx)
    assert [i["package"] for i in result["items"]] == ["six"]  # the root lock's 1.15.0, behind the latest
    audit = vulnerabilities.collect(ctx)  # the member's own packages, from the workspace lock
    assert (audit["packages"], audit["vulnerable_packages"]) == (1, 0)
    assert git(root, "status", "--porcelain", "--untracked-files=all") == ""


def test_pytest_coverage_and_deptry_on_a_python_repo(repo: Path) -> None:
    """The tests collector's pytest path (results, branch coverage, the floor) and deptry's findings."""
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text()
        + "dependencies = []\n\n[tool.pytest.ini_options]\npythonpath = ['src']\n\n[tool.coverage.run]\nbranch = true\n\n[tool.coverage.report]\nfail_under = 50\n"
    )
    (repo / "src" / "acme" / "fetch.py").write_text('"""Fetching."""\n\nimport requests  # not declared\n')
    (repo / "tests" / "test_pricing.py").write_text(
        "from acme.pricing import price\n\n\ndef test_price() -> None:\n    assert price(2, 1.5) == 3.0\n"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tests and an undeclared import")
    assert run_cq(repo, "run", "--tool", "tests", "--tool", "dependencies") == 0
    run_dir = latest_run(load_settings(repo).runs_dir)
    assert run_dir is not None
    snap = json.loads((run_dir / "snapshot.json").read_text())
    assert snap["errors"] == {}, snap["errors"]
    tests = snap["targets"]["acme"]["tests"]
    assert tests["results"]["passed"] == 1 and tests["results"]["failed"] == 0
    coverage = tests["coverage"]
    assert coverage["fail_under"] == pytest.approx(50)
    assert 0 < coverage["percent"] < 100 and coverage["branches"] > 0  # vip and unused_helper are never run
    assert "src/acme/pricing.py" in [f["file"] for f in coverage["worst_files"]]
    deptry = snap["targets"]["acme"]["dependencies"]
    assert [(i["code"], i["module"], i["file"]) for i in deptry["items"]] == [
        ("DEP001", "requests", "src/acme/fetch.py")
    ]


def test_version_and_cq_config(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("cq ")
    (repo / "quality").mkdir()
    (repo / "quality" / "cq.toml").write_text('data_dir = "data"\nname = "Acme"\n')
    monkeypatch.setenv("CQ_CONFIG", "quality/cq.toml")  # relative to the repo, as in CI or a cron job
    assert run_cq(repo, "run", "--tool", "size") == 0
    run_dir = latest_run(repo / "quality" / "data" / "runs")
    assert run_dir is not None
    assert "<title>Acme code quality</title>" in (run_dir / "dashboard.html").read_text()[:400]


def test_a_measurement_change_is_flagged_above_the_numbers(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = load_settings(repo)
    first = git(repo, "rev-list", "--max-parents=0", "HEAD")
    runner.save(runner.snapshot(settings, rev=first, tools=["size"]), settings, to_history=True)
    (repo / "cspell.json").write_text('{"words": ["acme"]}')  # the repo now has its own word list
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "words")
    run_cq(repo, "run", "--tool", "size", "--no-history")
    run_dir = latest_run(settings.runs_dir)
    assert run_dir is not None
    report = (run_dir / "report.md").read_text()
    assert "## Measurement changed since the previous snapshot" in report
    assert "cspell config changed (cspell.json)" in report
    assert report.index("Measurement changed") < report.index("## Since the previous snapshot")
    assert "cspell config changed" in (run_dir / "dashboard.html").read_text()


def test_the_latest_run_is_the_last_written_not_the_last_named(tmp_path: Path) -> None:
    for name in (
        "20261017T071548Z_ffffff0",
        "20261017T071548Z_0000000",
    ):  # same second, the later run's sha sorts first
        (tmp_path / name).mkdir()
        (tmp_path / name / "snapshot.json").write_text("{}")
    assert latest_run(tmp_path) == tmp_path / "20261017T071548Z_0000000"
    assert latest_run(tmp_path / "none") is None


def test_a_repo_with_only_the_repo_target_still_knows_when_it_is_dirty(tmp_path: Path) -> None:
    root = tmp_path / "docs-only"
    root.mkdir()
    (root / "README.md").write_text("# docs\n")
    git(root, "init", "-q", "-b", "trunk")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "docs")
    settings = load_settings(root)
    assert [t.name for t in settings.targets] == ["repo"]
    assert runner.commit_info("HEAD", settings)["dirty"] is False
    (root / "README.md").write_text("# docs, edited\n")
    assert runner.commit_info("HEAD", settings)["dirty"] is True


def test_backfill_walks_the_checked_out_branch_whatever_its_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "trunk-repo"
    root.mkdir()
    (root / "README.md").write_text("# x\n")
    git(root, "init", "-q", "-b", "trunk")  # no branch named main
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "one")
    assert run_cq(root, "backfill", "--since", "2000-01-01", "--dry-run") == 0
    assert run_cq(root, "backfill", "--since", "2000-01-01", "--branch", "trunk", "--dry-run") == 0
    assert capsys.readouterr().err.count("0 commit(s) to measure") == 2


def test_an_uncommitted_root_file_makes_a_monorepo_run_dirty(tmp_path: Path) -> None:
    root = tmp_path / "mono"
    (root / "apps" / "api" / "app").mkdir(parents=True)
    (root / "apps" / "api" / "app" / "__init__.py").write_text('"""Api."""\n')
    (root / "cq.toml").write_text('[[targets]]\nname = "api"\nkind = "python"\nroot = "apps/api"\nsource = ["app"]\n')
    (root / "README.md").write_text("# mono\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "mono")
    settings = load_settings(root)
    assert runner.commit_info("HEAD", settings)["dirty"] is False
    (root / "README.md").write_text("# mono, edited\n")  # outside every app, but the repo target reads it
    assert runner.commit_info("HEAD", settings)["dirty"] is True


def test_backfill_refuses_a_step_under_a_day(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cq(repo, "backfill", "--since", "2026-01-01", "--every", "0", "--dry-run") == 2
    assert "--every must be at least 1" in capsys.readouterr().err
    with pytest.raises(ValueError, match="at least a day apart"):
        runner.first_parent_commits("2026-01-01", -1, repo)


@pytest.mark.parametrize(
    ("lock", "first"),
    [("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm ci"), (None, "npm install")],
)
def test_a_worktree_installs_with_the_apps_own_package_manager(tmp_path: Path, lock: str | None, first: str) -> None:
    if lock:
        (tmp_path / lock).write_text("")
    assert " ".join(runner.install_command(tmp_path)).startswith(first)


def test_a_failed_install_loses_only_that_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ui = Target(name="ui", kind="typescript", root="ui", source=("src",), tests=False)
    (tmp_path / "old" / "ui").mkdir(parents=True)
    settings = load_settings(tmp_path)

    def fail(cmd: list[str], cwd: Path) -> str:
        raise runner.ToolError("pnpm: command not found")

    monkeypatch.setattr(runner, "run", fail)
    assert runner.prepare(ui, tmp_path / "old", settings) == "dependency install failed: pnpm: command not found"
    assert runner.prepare(ui, settings.repo_root, settings) is None  # the repo's own node_modules are assumed


def test_a_dirty_snapshot_does_not_count_as_the_commit_backfilled(repo: Path) -> None:
    settings = load_settings(repo)
    head = git(repo, "rev-parse", "HEAD")
    settings.history_dir.mkdir(parents=True)
    (settings.history_dir / f"2026-10-17_{head[:7]}-dirty.json").write_text("{}")
    assert commits_to_measure(settings, [head], None, 14, force=False) == [head]
    (settings.history_dir / f"2026-10-17_{head[:7]}.json").write_text("{}")
    assert commits_to_measure(settings, [head], None, 14, force=False) == []


def test_a_rerun_at_the_same_commit_keeps_both_history_entries(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every complete `cq run` adds its own entry (#54); an old-style name (no run time) still loads beside them."""
    # A complete run measures every collector; one keeps it complete and quick.
    monkeypatch.setattr(runner, "COLLECTORS", {"size": runner.COLLECTORS["size"]})
    settings = load_settings(repo)
    head = git(repo, "rev-parse", "--short=7", "HEAD")
    assert run_cq(repo, "run") == 0
    time.sleep(1.1)  # a run id has one-second resolution; real runs take longer than that
    assert run_cq(repo, "run") == 0
    entries = sorted(settings.history_dir.glob("*.json"))
    assert [runner.history_sha(p) for p in entries] == [head, head]
    assert all(p.stem.count("_") == 2 for p in entries)  # <commit date>_<sha>_<run time>
    first = entries[0]
    first.rename(first.with_name(f"{first.stem.rsplit('_', 1)[0]}.json"))  # as cq 0.12 named it
    assert run_cq(repo, "report") == 0
    run_dir = latest_run(settings.runs_dir)
    assert run_dir is not None
    assert "2 snapshot(s)" in (run_dir / "report.md").read_text()


def test_backfill_reads_the_commit_from_either_history_name(repo: Path) -> None:
    """A commit is done when a clean entry of either name form names it; a -dirty one never counts."""
    settings = load_settings(repo)
    head = git(repo, "rev-parse", "HEAD")
    settings.history_dir.mkdir(parents=True)
    (settings.history_dir / f"2026-10-17_{head[:7]}_20261017T120000Z-dirty.json").write_text("{}")
    assert commits_to_measure(settings, [head], None, 14, force=False) == [head]  # dirty never counts
    (settings.history_dir / f"2026-10-17_{head[:7]}_20261017T120000Z.json").write_text("{}")
    assert commits_to_measure(settings, [head], None, 14, force=False) == []


def test_init_runs_from_the_command_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cq(repo, "init", "--yes", "--skills", "none") == 0
    assert "[tool.cq" in (repo / "pyproject.toml").read_text()
    assert (repo / ".qlty" / "qlty.toml").exists()
    assert "Next: `cq doctor`, then `cq run`." in capsys.readouterr().out


def test_gate_runs_from_the_command_line(
    use_git: Callable[[Callable[..., str]], None], repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = {("merge-base",): "basesha1234", ("rev-parse",): "head123"}
    use_git(lambda *a: answers.get(a[:1], ""))
    monkeypatch.setattr(gate, "branch_secrets", lambda settings, base: [])
    monkeypatch.setattr(gate, "line_level", Mock(side_effect=ToolError("qlty printed no SARIF")))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert run_cq(repo, "gate", "--base", "origin/dev", "--report-only") == 0  # a tool error, but report-only
    text = next((repo / ".cq" / "runs").glob("*_gate_head123/gate.md")).read_text()
    assert "(warn mode)" in text and "1 tool errors" in text
    assert "`qlty`: qlty printed no SARIF" in text


def test_a_collector_that_fails_is_listed_as_a_tool_error(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(ctx: object) -> dict:
        raise RuntimeError("the tool crashed")

    monkeypatch.setattr(runner.COLLECTORS["size"], "collect", boom)
    assert run_cq(repo, "run", "--tool", "size") == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    snap = json.loads((run_dir / "snapshot.json").read_text())
    assert snap["errors"] == {"acme.size": "RuntimeError: the tool crashed"}
    assert "acme.size" not in snap["targets"]["acme"]
    assert "## Tool errors\n\n- `acme.size`: RuntimeError: the tool crashed" in (run_dir / "report.md").read_text()
    assert "ERROR RuntimeError: the tool crashed  (see acme/size.log)" in (run_dir / "run.log").read_text()


def fill_qlty_cache(repo: Path, cache: Path) -> None:
    """What qlty leaves under .qlty/ when it runs: links into its own cache, and plugin sources."""
    (repo / ".qlty").mkdir(exist_ok=True)
    for name in ("logs", "out", "results", "plugin_cachedir"):
        (cache / name).mkdir(parents=True, exist_ok=True)
        (repo / ".qlty" / name).symlink_to(cache / name)
    (repo / ".qlty" / "sources").mkdir(exist_ok=True)
    (repo / ".qlty" / "sources" / "plugin.toml").write_text("x = 1\n")


def test_init_keeps_qltys_cache_out_of_git(repo: Path, tmp_path: Path) -> None:
    assert run_cq(repo, "init", "--yes", "--skills", "none") == 0
    assert (repo / ".qlty" / ".gitignore").read_text() == (DEFAULTS_DIR / QLTY_IGNORE_DEFAULT).read_text()
    git(repo, "add", ".qlty")
    git(repo, "commit", "-q", "-m", "qlty config")
    fill_qlty_cache(repo, tmp_path / "qlty-cache")
    assert git(repo, "status", "--porcelain", "--", ".qlty") == ""
    (repo / ".qlty" / "configs").mkdir()
    (repo / ".qlty" / "configs" / ".ruff.toml").write_text("line-length = 100\n")
    assert git(repo, "status", "--porcelain", "--", ".qlty") == "?? .qlty/configs/"  # a plugin config is the repo's


def test_qltys_cache_never_makes_a_run_dirty(repo: Path, tmp_path: Path) -> None:
    (repo / ".qlty").mkdir()
    (repo / ".qlty" / "qlty.toml").write_text('config_version = "0"\n')
    git(repo, "add", ".qlty")
    git(repo, "commit", "-q", "-m", "qlty config, no .gitignore")
    settings = load_settings(repo)
    fill_qlty_cache(repo, tmp_path / "qlty-cache")  # untracked: this repo has no .qlty/.gitignore
    assert runner.commit_info("HEAD", settings)["dirty"] is False
    (repo / ".qlty" / "qlty.toml").write_text('config_version = "1"\n')
    assert runner.commit_info("HEAD", settings)["dirty"] is True  # the repo's own qlty config still counts
    git(repo, "checkout", "-q", "--", ".qlty/qlty.toml")
    (repo / ".qlty" / "configs").mkdir()
    (repo / ".qlty" / "configs" / ".ruff.toml").write_text("line-length = 100\n")
    assert runner.commit_info("HEAD", settings)["dirty"] is True  # and so do its plugin configs


def test_a_cut_list_is_counted_in_the_totals_report_and_dashboard(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def suppressions(ctx: Context) -> dict:
        items = [
            {"file": "src/acme/pricing.py", "line": n, "kind": "noqa", "reason": True, "text": f"# noqa: E{n} - why"}
            for n in range(10)
        ]
        return {"total": 10, "without_reason": 0, "by_kind": {"noqa": 10}, "items": ctx.cap(items, 3)}

    monkeypatch.setattr(runner.COLLECTORS["suppressions"], "collect", suppressions)
    assert run_cq(repo, "run", "--tool", "suppressions") == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    snap = json.loads((run_dir / "snapshot.json").read_text())
    assert snap["targets"]["acme"]["suppressions"]["truncated"] == {"items": 10}
    rows = [json.loads(line) for line in (run_dir / "findings.jsonl").read_text().splitlines()]
    assert sum(1 for r in rows if r["tool"] == "suppressions") == 3
    totals = json.loads((run_dir / "findings-totals.json").read_text())
    assert totals["targets"]["acme"]["suppressions"] == {"findings": 3, "cut": {"items": {"shown": 3, "total": 10}}}
    assert (
        "**Lists kept short** (count from the snapshot's totals, not the rows): `suppressions` items 3 of 10"
        in (run_dir / "report.md").read_text()
    )
    page = (run_dir / "dashboard.html").read_text()
    assert '"truncated":{"items":10}' in page and "function cutNote()" in page


def test_a_run_with_nothing_cut_says_nothing_about_cuts(repo: Path) -> None:
    assert run_cq(repo, "run", "--tool", "size") == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    assert "Lists kept short" not in (run_dir / "report.md").read_text()
    assert json.loads((run_dir / "findings-totals.json").read_text()) == {"targets": {}}


def test_the_dashboard_carries_the_report_for_viewers_without_scripts(repo: Path) -> None:
    assert run_cq(repo, "run", "--tool", "size") == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    page = (run_dir / "dashboard.html").read_text()
    static = page.split('<section class="panel" id="static-report">')[1].split("</section>")[0]
    assert "<script" not in static  # plain HTML, readable where scripts don't run
    assert "<h4>acme</h4>" in static and "<th>Metric</th>" in static and "Source lines" in static
    assert page.index('id="static-report"') < page.index('<script id="data"')
    assert page.index('classList.add("js")') < page.index("<main>")  # the switch runs before the page paints
    data = page.split('<script id="data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(data)["snapshot"]["run_id"] == run_dir.name
