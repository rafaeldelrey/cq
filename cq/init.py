"""``cq init``: set a repo up for cq, asking before every change.

Nothing here is required: in a repo with a pyproject.toml at its root, ``cq run`` detects its
targets without any setting. ``init`` adds what a repo keeps for good: a starter ``[tool.cq]`` (or
``cq.toml``) with the gate, and qlty's config, without which the qlty-based tools report an error.
"""

import shutil

from cq.config import CONFIG_FILE, DEFAULTS_DIR, Settings, find_config
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
PLUGIN_INSTALL = "/plugin marketplace add rafaeldelrey/cq\n/plugin install cq@cq"


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


def describe(settings: Settings) -> None:
    """What cq would measure in this repo."""
    print(f"Repo: {settings.repo_root}")
    print(f"Settings: {settings.config_file or 'none (targets detected)'}")
    for t in settings.targets:
        detail = "" if t.kind == "repo" else f": {', '.join(t.source)}{', tests/' if t.tests else ''}"
        print(f"  {t.name} ({t.kind}, root {t.root}){detail}")
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


def init(settings: Settings, *, assume_yes: bool = False) -> int:
    """Describe the repo, then offer the settings and qlty's config; print the plugin's install line."""
    describe(settings)
    print()
    add_settings(settings, assume_yes=assume_yes)
    add_qlty(settings, assume_yes=assume_yes)
    print(f"\nThe /code-quality and /setup-cq skills come as a Claude Code plugin. In Claude Code:\n{PLUGIN_INSTALL}")
    print("A team gets it by enabling the plugin in the repo's .claude/settings.json.")
    print("\nNext: `cq doctor`, then `cq run`.")
    return 0
