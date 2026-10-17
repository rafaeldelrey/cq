"""The cq command end to end, on real throwaway git repos, with the tools that need nothing installed."""

import json
import subprocess
from pathlib import Path

import pytest

from cq import cli, complexity_gate, runner
from cq.backfill import commits_to_measure
from cq.config import load_settings
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
    assert paths[-1].name.endswith(f"_{first[:7]}.json")


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
    assert [p.name.endswith(f"_{first[:7]}.json") for p in settings.history_dir.glob("*.json")] == [True]


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
    assert measured["freshness"]["outdated"] >= 0


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
