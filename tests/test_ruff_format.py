"""The ruff collector's unformatted-files count, across the ruff versions an app may pin.

ruff 0.16 changed `ruff format --check`'s output: the "Would reformat: <path>" lines became
diagnostics, and a count read from them dropped to 0 (#57). The summary line stayed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from cq import cli
from cq.collectors import ruff
from cq.proc import ToolError
from cq.report import latest_run

UNFORMATTED = {"a.py": "x=1\n", "b.py": "y  =  2\n", "c.py": "z = 3\n"}  # two to reformat, one formatted


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not installed")
@pytest.mark.parametrize("version", ["0.15.0", "0.16.6"])
def test_the_count_reads_each_ruff_versions_output(tmp_path: Path, version: str) -> None:
    for name, text in UNFORMATTED.items():
        (tmp_path / name).write_text(text)
    out = subprocess.run(
        ["uvx", "-q", f"ruff@{version}", "format", "--check", "."], cwd=tmp_path, capture_output=True, text=True
    )
    assert out.returncode == 1
    assert ruff.unformatted_count(out.stdout) == 2


@pytest.mark.parametrize(
    ("output", "count"),
    [("3 files already formatted\n", 0), ("", 0), ("1 file would be reformatted, 2 files already formatted\n", 1)],
)
def test_the_count_from_the_summary_line(output: str, count: int) -> None:
    assert ruff.unformatted_count(output) == count


def test_files_to_reformat_without_a_readable_count_is_a_tool_error() -> None:
    with pytest.raises(ToolError, match="no count cq can read"):
        ruff.unformatted_count("unformatted: File would be reformatted\n --> a.py:1:2\n")


def test_cq_run_counts_unformatted_files(tmp_path: Path) -> None:
    """End to end, with whatever ruff the app's environment runs (here, the latest)."""
    repo = tmp_path / "acme"
    (repo / "acme").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'acme'\nversion = '0.1.0'\ndependencies = []\n")
    (repo / "acme" / "__init__.py").write_text('"""Acme."""\n')
    (repo / "acme" / "messy.py").write_text('"""Messy."""\n\nx=1\n')
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], cwd=repo, check=True)
    assert cli.main(["--repo", str(repo), "run", "--tool", "ruff", "--no-history"]) == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    snapshot = (run_dir / "snapshot.json").read_text()
    assert '"unformatted_files": 1' in snapshot
