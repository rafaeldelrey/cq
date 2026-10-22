"""ruff and pyright count only their own target's files (#47, #48), through a real `cq run`."""

import json
import subprocess
from pathlib import Path

import pytest

from cq import cli
from cq.collectors import ruff as ruff_collector
from cq.config import load_settings
from cq.context import Context
from cq.proc import ToolError
from cq.report import latest_run

# One ruff violation (F401), one unformatted line and one type error per module.
MODULE = '"""{name}."""\n\nimport os\n\nx: int = "a"\ny=1\n'


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


def measured(repo: Path) -> dict:
    """``cq run --tool ruff --tool pyright`` on ``repo``: the snapshot's targets."""
    assert cli.main(["--repo", str(repo), "run", "--tool", "ruff", "--tool", "pyright", "--no-history"]) == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    snapshot = json.loads((run_dir / "snapshot.json").read_text())
    assert snapshot["errors"] == {}, snapshot["errors"]
    return snapshot["targets"]


def pyproject(name: str) -> str:
    """A dependency-free project."""
    return f"[project]\nname = '{name}'\nversion = '0.1.0'\nrequires-python = '>=3.12'\ndependencies = []\n"


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


def test_a_nested_targets_files_count_toward_it_alone(tmp_path: Path) -> None:
    repo = committed(
        tmp_path / "r",
        {
            "cq.toml": TARGETS,
            "apps/outer/pyproject.toml": pyproject("outer"),
            "apps/outer/outer/__init__.py": MODULE.format(name="Outer"),
            # A folder named like the nested target, inside the outer one: still the outer's.
            "apps/outer/outer/inner/__init__.py": MODULE.format(name="Decoy"),
            "apps/outer/inner/pyproject.toml": pyproject("inner"),
            "apps/outer/inner/inner/__init__.py": MODULE.format(name="Inner"),
        },
    )
    targets = measured(repo)
    expected = {
        "outer": (2, 2, 2, ["apps/outer/outer/__init__.py", "apps/outer/outer/inner/__init__.py"]),
        "inner": (1, 1, 1, ["apps/outer/inner/inner/__init__.py"]),
    }
    for name, (violations, unformatted, errors, files) in expected.items():
        ruff, pyright = targets[name]["ruff"], targets[name]["pyright"]
        assert (ruff["violations"], ruff["unformatted_files"], pyright["errors"]) == (
            violations,
            unformatted,
            errors,
        ), name
        assert sorted(i["file"] for i in pyright["items"]) == files, name


def test_a_nested_target_with_glob_characters_in_its_name_excludes_only_itself(tmp_path: Path) -> None:
    """ruff reads excludes as globs: "inner[1]" must not match the outer target's "inner1"."""
    targets = TARGETS.replace('root = "apps/outer/inner"', 'root = "apps/outer/inner[1]"')
    repo = committed(
        tmp_path / "r",
        {
            "cq.toml": targets,
            "apps/outer/pyproject.toml": pyproject("outer"),
            "apps/outer/outer/__init__.py": MODULE.format(name="Outer"),
            "apps/outer/inner1/mod.py": MODULE.format(name="Decoy"),
            "apps/outer/inner[1]/pyproject.toml": pyproject("inner"),
            "apps/outer/inner[1]/inner/__init__.py": MODULE.format(name="Inner"),
        },
    )
    ruff = {name: target["ruff"] for name, target in measured(repo).items() if name != "repo"}
    assert (ruff["outer"]["violations"], ruff["outer"]["unformatted_files"]) == (2, 2)
    # The same counts with the glob unescaped (inner1 hidden, inner[1] counted): the files tell.
    assert sorted(i["file"] for i in ruff["outer"]["items"]) == [
        "apps/outer/inner1/mod.py",
        "apps/outer/outer/__init__.py",
    ]
    assert (ruff["inner"]["violations"], ruff["inner"]["unformatted_files"]) == (1, 1)


def test_glob_literal_escapes_each_glob_character() -> None:
    assert ruff_collector.glob_literal("a[1]/b*c?{d}") == "a[[]1[]]/b[*]c[?][{]d[}]"


def test_a_nested_target_whose_path_has_a_comma_is_a_tool_error() -> None:
    """ruff splits --extend-exclude on commas: an exclusion it can't express is an error, not a miscount."""
    with pytest.raises(ToolError, match="has a comma"):
        ruff_collector.glob_literal("a,b")


def test_a_target_inheriting_a_parent_pyright_config_counts_only_its_own_files(tmp_path: Path) -> None:
    """The app has no [tool.pyright]: pyright uses the parent's, which also covers other/."""
    repo = committed(
        tmp_path / "r",
        {
            "cq.toml": '[[targets]]\nname = "a"\nkind = "python"\nroot = "apps/a"\nsource = ["a"]\n',
            "pyproject.toml": "[tool.pyright]\ninclude = ['.']\n",
            "apps/a/pyproject.toml": pyproject("a"),
            "apps/a/a/__init__.py": MODULE.format(name="A"),
            "other/b.py": MODULE.format(name="B"),
        },
    )
    pyright = measured(repo)["a"]["pyright"]
    assert pyright["errors"] == 1
    assert [i["file"] for i in pyright["items"]] == ["apps/a/a/__init__.py"]


def test_owns_and_nested_roots(tmp_path: Path) -> None:
    repo = committed(tmp_path / "r", {"cq.toml": TARGETS, "apps/outer/x.py": "", "apps/outer/inner/y.py": ""})
    settings = load_settings(repo)
    outer = next(t for t in settings.targets if t.name == "outer")
    ctx = Context(target=outer, checkout=repo, raw_dir=tmp_path, settings=settings)
    assert ctx.nested_roots == [(repo / "apps" / "outer" / "inner").resolve()]
    assert ctx.owns("x.py") and ctx.owns(repo / "apps" / "outer" / "x.py")
    assert not ctx.owns("inner/y.py") and not ctx.owns(repo / "elsewhere.py")
