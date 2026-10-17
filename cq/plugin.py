"""cq's Claude Code plugin (the /code-quality and /setup-cq skills): where it's installed, and installing it.

The plugin lives in cq's own repo, which is also its marketplace. It is installed with the
``claude`` CLI, for the user (every repo, nothing written to this one) or for the project (the
repo's .claude/settings.json, committed, so the team gets it).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

MARKETPLACE = "rafaeldelrey/cq"
PLUGIN = "cq@cq"
PASTE = f"/plugin marketplace add {MARKETPLACE}\n/plugin install {PLUGIN}"
Scope = Literal["user", "project"]


def claude_cli() -> str | None:
    """The ``claude`` command, or None when Claude Code isn't installed."""
    return shutil.which("claude")


def capture(cmd: list[str], cwd: Path) -> str | None:
    """A command's stdout, or None when it failed, hung or isn't installed (the caller then knows nothing)."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=60)
    except subprocess.TimeoutExpired:  # a claude that prompts or hangs: treated as "can't tell"
        return None
    except FileNotFoundError:  # the command isn't installed (git, on a fresh machine): "can't tell"
        return None
    return proc.stdout if proc.returncode == 0 else None


def call(cmd: list[str], cwd: Path) -> int:
    """Run a command with its output going straight to the terminal, as it would by hand."""
    sys.stdout.flush()  # what cq printed so far comes first, even through a pipe
    try:
        return subprocess.run(cmd, cwd=cwd, check=False, timeout=600).returncode
    except subprocess.TimeoutExpired:  # a clone or download that stalled: that step failed, init goes on
        print(f"{' '.join(cmd[:3])} timed out after 10 minutes.")
        return 1


def installed(claude: str, repo_root: Path) -> dict[str, str]:
    """Where cq's plugin is installed that counts for this repo: scope → version.

    ``user`` counts everywhere; ``project`` and ``local`` count when their project is this repo.
    Unknown (claude failed, odd output) is an empty dict: the question is asked rather than skipped.
    """
    out = capture([claude, "plugin", "list", "--json"], repo_root)
    try:
        rows = json.loads(out) if out else []
    except json.JSONDecodeError:  # output claude didn't mean as JSON: treated as "can't tell"
        return {}
    found: dict[str, str] = {}
    here = repo_root.resolve()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("id") != PLUGIN:
            continue
        scope = row.get("scope", "")
        project = row.get("projectPath")
        if scope == "user" or (project and Path(project).resolve() == here):
            found[scope] = str(row.get("version", ""))
    return found


def commands(claude: str, scope: Scope) -> list[list[str]]:
    """The marketplace, then the plugin, both at ``scope``."""
    return [
        [claude, "plugin", "marketplace", "add", MARKETPLACE, "--scope", scope],
        [claude, "plugin", "install", PLUGIN, "--scope", scope],
    ]


def install(claude: str, scope: Scope, repo_root: Path) -> bool:
    """Run the install commands in order; False at the first that fails."""
    return all(call(cmd, repo_root) == 0 for cmd in commands(claude, scope))


def repo_root_of(folder: Path) -> Path:
    """The git repo ``folder`` is in (a project install is keyed by the repo's root), or the folder."""
    top = capture(["git", "rev-parse", "--show-toplevel"], folder)
    return Path(top.strip()) if top else folder


def status(folder: Path) -> tuple[bool, str, str]:
    """For doctor, run anywhere in a repo: (installed, what was found, the fix)."""
    claude = claude_cli()
    if claude is None:
        return False, "Claude Code not installed", "in Claude Code: " + PASTE.replace("\n", ", then ")
    found = installed(claude, repo_root_of(folder))
    if not found:
        return False, "not installed", "cq init"
    where = ", ".join(f"{scope} {version}" for scope, version in sorted(found.items()))
    return True, f"{PLUGIN} ({where})", ""
