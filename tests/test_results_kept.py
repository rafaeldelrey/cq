"""Results a tool produced stay in the snapshot when its exit code or output is unusual (#49, #52).

- ruff format exits 2 when a file can't be parsed, after counting the others (#49).
- pytest drops its summary line at -qq, which a ``-q`` in the project's addopts used to cause (#52).

Each runs the real tool through ``cq run``; uv fetches the pinned ruff and pytest when they aren't cached.
"""

import json
import subprocess
from pathlib import Path

import pytest

from cq import cli
from cq.collectors import ruff
from cq.config import load_settings
from cq.context import Context
from cq.proc import ToolError
from cq.report import latest_run

UNPARSABLE = "x = (1,\n"  # a syntax error on line 1, reported at the end of the file
MESSY = '"""Messy."""\n\nimport os\n\nx=1\n'  # one F401 and one file to reformat


def committed(root: Path, files: dict[str, str]) -> Path:
    """A git repo at ``root`` holding ``files``, all committed."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], cwd=root, check=True)
    return root


def measured(repo: Path, tool: str) -> dict:
    """``cq run --tool <tool>`` on ``repo``: the snapshot, which must have no tool error."""
    assert cli.main(["--repo", str(repo), "run", "--tool", tool, "--no-history"]) == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    snapshot = json.loads((run_dir / "snapshot.json").read_text())
    assert snapshot["errors"] == {}, snapshot["errors"]
    return snapshot["targets"]


def pyproject(name: str, extra: str = "") -> str:
    """A dependency-free project, plus ``extra`` TOML."""
    return f"[project]\nname = '{name}'\nversion = '0.1.0'\nrequires-python = '>=3.12'\ndependencies = []\n{extra}"


def test_an_unparsable_file_keeps_ruff_s_results(tmp_path: Path) -> None:
    repo = committed(
        tmp_path / "acme",
        {
            "pyproject.toml": pyproject("acme"),
            "acme/__init__.py": '"""Acme."""\n',
            "acme/broken.py": UNPARSABLE,
            "acme/messy.py": MESSY,
        },
    )
    result = measured(repo, "ruff")["acme"]["ruff"]
    assert result["unformatted_files"] == 1  # messy.py; broken.py can't be formatted at all
    found = {(i["file"], i["code"]) for i in result["items"]}
    assert ("acme/messy.py", "F401") in found
    (syntax,) = [i for i in result["items"] if i["file"] == "acme/broken.py"]
    assert syntax["line"] >= 1 and syntax["msg"]


TARGETS = """
[[targets]]
name = "outer"
kind = "python"
root = "apps/outer"
source = ["outer"]

[[targets]]
name = "inner"
kind = "python"
root = "apps/outer/inner"
source = ["inner"]
"""


@pytest.mark.parametrize("pin", ["ruff==0.15.0", "ruff"], ids=["ruff-0.15-fallback", "latest-ruff"])
def test_an_unparsable_file_in_an_outer_target_keeps_its_own_count(tmp_path: Path, pin: str) -> None:
    """With a nested target: ruff 0.15 takes the per-file fallback (#48), ruff 0.16+ --extend-exclude.
    Both exit 2 the same way."""
    repo = committed(
        tmp_path / "r",
        {
            "cq.toml": TARGETS,
            "apps/outer/pyproject.toml": pyproject("outer", f"\n[dependency-groups]\ndev = ['{pin}']\n"),
            "apps/outer/outer/__init__.py": MESSY,
            "apps/outer/outer/broken.py": UNPARSABLE,
            "apps/outer/inner/pyproject.toml": pyproject("inner"),
            "apps/outer/inner/inner/__init__.py": MESSY,
        },
    )
    outer = measured(repo, "ruff")["outer"]["ruff"]
    assert outer["unformatted_files"] == 1  # its own messy file, not the inner target's
    assert {i["file"] for i in outer["items"]} == {"apps/outer/outer/__init__.py", "apps/outer/outer/broken.py"}


def test_an_unparsable_file_beside_formatted_ones_counts_zero(tmp_path: Path) -> None:
    """Exit 2 whose summary says only "already formatted"."""
    repo = committed(
        tmp_path / "acme",
        {"pyproject.toml": pyproject("acme"), "acme/__init__.py": '"""Acme."""\n', "acme/broken.py": UNPARSABLE},
    )
    assert measured(repo, "ruff")["acme"]["ruff"]["unformatted_files"] == 0


def test_ruff_exit_2_without_a_summary_is_still_a_tool_error_with_ruff_s_message(tmp_path: Path) -> None:
    repo = committed(
        tmp_path / "acme",
        {"pyproject.toml": pyproject("acme", "\n[tool.ruff]\nline-length = 'wide'\n"), "acme/__init__.py": ""},
    )
    settings = load_settings(repo)
    ctx = Context(target=settings.targets[0], checkout=repo, raw_dir=tmp_path, settings=settings)
    with pytest.raises(ToolError, match="line-length") as excinfo:
        ruff.format_count(ctx, [])
    assert excinfo.value.returncode == 2


@pytest.mark.parametrize(
    "output",
    [
        "invalid-syntax: unexpected EOF while parsing\n --> a.py:1:9\n\n2 files already formatted\n",
        "2 files already formatted\n",
    ],
)
def test_the_summary_after_diagnostics_is_still_read(output: str) -> None:
    assert ruff.unformatted_count(output) == 0


TESTS = "def test_ok():\n    assert True\n\n\ndef test_fails():\n    assert 1 == 2\n"
WARNS = "import warnings\n\n\ndef test_warns():\n    for i in range(60):\n        warnings.warn(f'old api {i}', DeprecationWarning)\n"


@pytest.mark.parametrize(
    ("addopts", "files", "counts"),
    [
        ('addopts = "-q --tb=short"', {"tests/test_a.py": TESTS}, (1, 1)),
        ('addopts = "-qq"', {"tests/test_a.py": TESTS}, (1, 1)),
        ("", {"tests/test_a.py": TESTS, "tests/test_w.py": WARNS}, (2, 1)),
    ],
    ids=["addopts-q", "addopts-qq", "long-warnings-summary"],
)
def test_pytest_results_are_read_whatever_the_project_s_verbosity(
    tmp_path: Path, addopts: str, files: dict[str, str], counts: tuple[int, int]
) -> None:
    settings = f"\n[tool.pytest.ini_options]\n{addopts}\n" if addopts else ""
    repo = committed(
        tmp_path / "acme",
        {
            "pyproject.toml": pyproject("acme", settings),
            "acme/__init__.py": '"""Acme."""\n\nONE = 1\n',
            "conftest.py": "",
            **files,
        },
    )
    (repo / "cq.toml").write_text(
        '[[targets]]\nname = "acme"\nkind = "python"\nroot = "."\nsource = ["acme"]\ntests = true\n'
    )
    tests = measured(repo, "tests")["acme"]["tests"]
    assert (tests["results"]["passed"], tests["results"]["failed"]) == counts
    assert tests["coverage"]["statements"] > 0
