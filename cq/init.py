"""``cq init``: set a repo up for cq, asking before every change.

Nothing here is required: in a repo with a pyproject.toml at its root, ``cq run`` detects its
targets without any setting. ``init`` adds what a repo keeps for good: a starter ``[tool.cq]`` (or
``cq.toml``) with the gate, and qlty's config, without which the qlty-based tools report an error.
It also offers cq's Claude Code plugin, for the user or for the repo.
"""

# aislop-ignore-file ai-slop/python-print-debug -- `cq init` talks to the user: its prints are the interface

import shutil
import sys

from cq import plugin
from cq.config import CONFIG_FILE, DEFAULTS_DIR, Settings, Target, find_config
from cq.doctor import confirm

# The gate's recommended blocking list: what a branch should never add.
BLOCK = (
    "secrets:*",
    "aislop:ai-slop/*",
    "vale:Stubs",
    "semgrep:cq-*",
    "qlty:zizmor:*",
    "qlty:shellcheck:*",
    "qlty:hadolint:*",
    "qlty:osv-scanner:*",
    "smells:*",
    "pyscn:*",
    "ratchet:*",
    "docstrings:missing-docstring",
)
SKILLS_CHOICES = ("user", "project", "none")
ANSWERS = {"u": "user", "p": "project", "s": "none"}


def starter(prefix: str) -> str:
    """A starter settings block: the gate, and the targets left to detection.

    ``prefix`` is "tool.cq." in a pyproject.toml and "" in cq.toml, whose keys sit at the top level.
    """
    block = "\n".join(f'    "{b}",' for b in BLOCK)
    header = "\n[tool.cq]" if prefix else ""
    return f"""{header}
# cq's settings (https://github.com/rafaeldelrey/cq). Targets are detected: a pyproject.toml at the
# root is one Python app. To list them yourself, add [[{prefix}targets]] entries
# (name, kind = "python" | "typescript", root, source, tests).

[{prefix}gate]
mode = "warn"  # "block": the gate fails the job on a blocking finding
block = [
{block}
]
"""


def target_detail(target: Target) -> str:
    """An app's production paths, and tests/ when it has tests; nothing for the repo target."""
    if target.kind == "repo":
        return ""
    tests = ", tests/" if target.tests else ""
    return f": {', '.join(target.source)}{tests}"


def describe(settings: Settings) -> None:
    """What cq would measure in this repo."""
    print(f"Repo: {settings.repo_root}")
    print(f"Settings: {settings.config_file or 'none (targets detected)'}")
    for t in settings.targets:
        print(f"  {t.name} ({t.kind}, root {t.root}){target_detail(t)}")
    print(f"Data: {settings.data_dir} (history/ and runs/, never committed)")


def add_settings(settings: Settings, *, assume_yes: bool) -> None:
    """A starter [tool.cq] in pyproject.toml, or cq.toml when there's no pyproject."""
    root = settings.repo_root
    _, source = find_config(root)
    if source is not None:
        print(f"Settings already in {source.name}.")
        return
    pyproject = root / "pyproject.toml"
    target, prefix = (pyproject, "tool.cq.") if pyproject.exists() else (root / CONFIG_FILE, "")
    if not confirm(f"Add cq's starter settings (the gate, targets detected) to {target.name}?", assume_yes=assume_yes):
        return
    with target.open("a") as fh:
        fh.write(starter(prefix))
    print(f"Wrote cq's settings to {target.name}.")


def add_qlty(settings: Settings, *, assume_yes: bool) -> None:
    """cq's default .qlty/qlty.toml, when the repo has no qlty config."""
    dest = settings.repo_root / ".qlty" / "qlty.toml"
    if dest.exists():
        print("qlty config already in .qlty/qlty.toml.")
        return
    question = "Add qlty's config (.qlty/qlty.toml, cq's default)? Without it radarlint, smells and the gate's line checks don't run"
    if not confirm(question, assume_yes=assume_yes):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEFAULTS_DIR / "qlty.toml", dest)
    print("Wrote .qlty/qlty.toml.")


SCOPE_NOTES: tuple[tuple[str, plugin.Scope, str], ...] = (
    ("u", "user", "just you, in every repo; nothing written here"),
    ("p", "project", "this repo: writes .claude/settings.json, commit it and the team gets it"),
)


def ask_scope() -> str:
    """Where to install the plugin: user, project or none; none without a terminal to ask on."""
    print("\nInstall the cq skills for Claude Code (/code-quality, /setup-cq)?")
    for key, scope, note in SCOPE_NOTES:
        runs = " && ".join(" ".join(cmd) for cmd in plugin.commands("claude", scope))
        print(f"  {key}  {note}\n       runs: {runs}")
    print("  s  skip")
    if not sys.stdin.isatty():
        print("[u/p/s] s (not a terminal: pass --skills user|project|none)")
        return "none"
    answer = input("[u/p/s] ").strip().lower()[:1]
    if answer not in ANSWERS:
        print(f"{answer or 'No answer'}: not u, p or s, so skipped.")
    return ANSWERS.get(answer, "none")


def add_plugin(settings: Settings, *, skills: str | None, assume_yes: bool) -> None:
    """Offer cq's Claude Code plugin, unless it's already installed for this repo or the user."""
    claude = plugin.claude_cli()
    if claude is None:
        print(
            f"\nThe cq skills come as a Claude Code plugin. Claude Code isn't on the PATH here; in Claude Code:\n{plugin.PASTE}"
        )
        return
    found = plugin.installed(claude, settings.repo_root)
    here = {"project", "local"} & set(found)
    # An explicit --skills project is the team install: only a project install (the committed
    # .claude/settings.json) already gives it; a user or local one (settings.local.json) doesn't.
    if found and (skills != "project" or "project" in found):
        where = "for this repo" if here else "for you"
        print(f"\ncq's Claude Code plugin is already installed {where}.")
        return
    choice = skills or ("user" if assume_yes else ask_scope())
    if choice == "none":
        print("Skipped the Claude Code plugin; run `cq init` again to install it.")
        return
    scope: plugin.Scope = "project" if choice == "project" else "user"
    if not plugin.install(claude, scope, settings.repo_root):
        print(f"The plugin wasn't installed (see claude's output above). In Claude Code instead:\n{plugin.PASTE}")
        return
    if scope == "project":
        print("Installed for this repo: commit .claude/settings.json, and the team gets the skills.")
    else:
        print("Installed for you: /code-quality and /setup-cq work in every repo.")


def init(settings: Settings, *, assume_yes: bool = False, skills: str | None = None) -> int:
    """Describe the repo, then offer the settings, qlty's config and the Claude Code plugin."""
    describe(settings)
    print()
    add_settings(settings, assume_yes=assume_yes)
    add_qlty(settings, assume_yes=assume_yes)
    add_plugin(settings, skills=skills, assume_yes=assume_yes)
    print("\nNext: `cq doctor`, then `cq run`.")
    return 0
