"""deptry on a target with no manifest (#58): skipped with the reason, not a tool error."""

import json
import subprocess
from pathlib import Path

from cq import cli
from cq.report import latest_run
from cq.requirements import SKIP_NO_MANIFEST

SETTINGS = """
[[targets]]
name = "bare"
kind = "python"
root = "apps/bare"
source = ["bare"]

[[targets]]
name = "app"
kind = "python"
root = "apps/app"
source = ["app"]

[[targets]]
name = "tools"
kind = "python"
root = "apps/tools"
source = ["tools"]
"""


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


def test_a_target_without_a_manifest_is_skipped_and_the_others_measured(tmp_path: Path) -> None:
    repo = committed(
        tmp_path / "demo",
        {
            "cq.toml": SETTINGS,
            "apps/bare/bare/__init__.py": '"""Bare."""\n\nimport json\n',
            "apps/app/pyproject.toml": (
                "[project]\nname = 'app'\nversion = '0.1.0'\nrequires-python = '>=3.12'\ndependencies = []\n"
            ),
            "apps/app/app/__init__.py": '"""App."""\n\nimport yaml\n',  # imported, never declared: DEP001
            "apps/tools/requirements.in": "requests\n",  # pip-tools' input, which deptry reads on its own
            "apps/tools/tools/__init__.py": '"""Tools."""\n\nimport yaml\n',
        },
    )
    assert cli.main(["--repo", str(repo), "run", "--tool", "dependencies", "--no-history"]) == 0
    run_dir = latest_run(repo / ".cq" / "runs")
    assert run_dir is not None
    snapshot = json.loads((run_dir / "snapshot.json").read_text())
    assert snapshot["errors"] == {}
    assert snapshot["targets"]["bare"]["dependencies"]["skipped"] == SKIP_NO_MANIFEST
    app = snapshot["targets"]["app"]["dependencies"]
    assert (app["missing"], [i["module"] for i in app["items"]]) == (1, ["yaml"])
    tools = snapshot["targets"]["tools"]["dependencies"]
    assert sorted((i["code"], i["module"]) for i in tools["items"]) == [("DEP001", "yaml"), ("DEP002", "requests")]
