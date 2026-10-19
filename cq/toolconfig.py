"""Which config file each tool reads, in layers.

1. A file the settings name (``semgrep_rules``, ``cspell_config``, …), relative to the settings' folder.
2. The repo's own config for the tool: ``[tool.vulture]`` in the app's or the root's pyproject,
   ``cspell.json``, ``.vale.ini``, ``.gitleaks.toml``, ``.semgrep/``, ``.importlinter``.
3. cq's packaged default (cq/defaults/).

Files are looked up in the live repo, not in a checkout of an old commit: a backfill measures
every revision with today's config, so old and new points stay comparable.
"""

import hashlib
import tomllib
from pathlib import Path

from cq.config import DEFAULTS_DIR, PYPROJECT, Settings, Target


def named(settings: Settings, value: str) -> Path | None:
    """A file the settings name, or None when they name none."""
    return (settings.config_dir / value).resolve() if value else None


def first_existing(root: Path, candidates: tuple[str, ...]) -> Path | None:
    """The first of ``candidates`` present under ``root``."""
    return next((root / c for c in candidates if (root / c).exists()), None)


def has_table(pyproject: Path, tool: str) -> bool:
    """Whether a pyproject.toml configures ``[tool.<tool>]``."""
    if not pyproject.exists():
        return False
    return tool in tomllib.loads(pyproject.read_text()).get("tool", {})


def pyproject_tool(settings: Settings, target: Target, tool: str, setting: str) -> Path:
    """A tool configured in a toml: the file the settings name, the app's or the root's pyproject, or cq's default."""
    if named_file := named(settings, setting):
        return named_file
    for pyproject in (target.app_dir(settings.repo_root) / PYPROJECT, settings.repo_root / PYPROJECT):
        if has_table(pyproject, tool):
            return pyproject
    return DEFAULTS_DIR / f"{tool}.toml"


def vulture_config(settings: Settings, target: Target) -> Path:
    """The pyproject or toml holding ``[tool.vulture]`` for this app."""
    return pyproject_tool(settings, target, "vulture", settings.vulture_config)


def vulture_whitelist(settings: Settings, target: Target) -> Path | None:
    """The app's vulture whitelist, when it keeps one (names used only by frameworks or tests)."""
    return first_existing(target.app_dir(settings.repo_root), ("vulture_whitelist.py", "whitelist.py"))


def interrogate_config(settings: Settings, target: Target) -> Path:
    """The pyproject or toml holding ``[tool.interrogate]`` for this app."""
    return pyproject_tool(settings, target, "interrogate", settings.interrogate_config)


def cspell_config(settings: Settings) -> Path:
    """The cspell config: named, the repo's, or cq's default (no project words)."""
    candidates = ("cspell.json", ".cspell.json", "cspell.config.json", "cspell.config.yaml", ".cspell.yaml")
    return (
        named(settings, settings.cspell_config)
        or first_existing(settings.repo_root, candidates)
        or DEFAULTS_DIR / "cspell.json"
    )


def vale_config(settings: Settings) -> Path:
    """The vale config: named, the repo's .vale.ini, or cq's AISlop style."""
    return (
        named(settings, settings.vale_config)
        or first_existing(settings.repo_root, (".vale.ini",))
        or DEFAULTS_DIR / "vale" / ".vale.ini"
    )


def gitleaks_config(settings: Settings) -> Path:
    """The secrets scanner's config: named, the repo's, or gitleaks' own rules."""
    candidates = (".gitleaks.toml", ".betterleaks.toml", "gitleaks.toml")
    return (
        named(settings, settings.gitleaks_config)
        or first_existing(settings.repo_root, candidates)
        or DEFAULTS_DIR / "gitleaks.toml"
    )


def semgrep_rules(settings: Settings) -> Path:
    """The house-rules folder: production rules in its *.yml, test rules in tests/*.yml."""
    return (
        named(settings, settings.semgrep_rules)
        or first_existing(settings.repo_root, (".semgrep",))
        or DEFAULTS_DIR / "semgrep"
    )


def import_contracts(settings: Settings, target: Target) -> Path | None:
    """The app's import-linter contracts: ``<contracts_dir>/<target>.ini``, or the app's own config."""
    folder = named(settings, settings.contracts_dir)
    if folder is not None:
        config = folder / f"{target.name}.ini"
        return config if config.exists() else None
    app = target.app_dir(settings.repo_root)
    if has_table(app / PYPROJECT, "importlinter"):
        return app / PYPROJECT
    return first_existing(app, (".importlinter",))


def qlty_config(settings: Settings) -> Path | None:
    """The repo's .qlty/qlty.toml; qlty needs one in the repo (``cq init`` writes cq's default)."""
    return first_existing(settings.repo_root, (".qlty/qlty.toml",))


# What the tools run in an app's own environment read: its configs, and the lockfile that pins the
# tools themselves (ruff, pyright, pytest, eslint, tsc, vitest), so a version bump shows as a change.
APP_INPUTS = (
    PYPROJECT,
    "uv.lock",
    "pyrightconfig.json",
    "ruff.toml",
    ".ruff.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    ".coveragerc",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "tsconfig.json",
    "eslint.config.js",
    "eslint.config.mjs",
    ".eslintrc.json",
    "vitest.config.ts",
    "vitest.config.mts",
)


def app_inputs(settings: Settings, target: Target) -> dict[str, Path]:
    """The app's own config and lockfiles that exist, keyed ``<target>.app:<file>``."""
    app = target.app_dir(settings.repo_root)
    return {f"{target.name}.app:{name}": app / name for name in APP_INPUTS if (app / name).is_file()}


def digest(path: Path) -> str:
    """A short hash of a config file, or of every file in a config folder."""
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    h = hashlib.sha256()
    for f in files:
        h.update(f.relative_to(path).as_posix().encode() if path.is_dir() else b"")
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


def label(settings: Settings, path: Path) -> str:
    """Where a config came from, for the snapshot: repo-relative, or ``cq:<default>``."""
    if path.is_relative_to(DEFAULTS_DIR):
        return f"cq:{path.relative_to(DEFAULTS_DIR).as_posix()}"
    if path.is_relative_to(settings.repo_root):
        return path.relative_to(settings.repo_root).as_posix()
    return path.as_posix()


def used_configs(settings: Settings) -> dict[str, dict[str, str]]:
    """Every config file the tools read, with where it came from and its hash.

    Stored in the snapshot, so a step in the trend that a config change made (an allowlist grew, a
    rule went away) is told apart from a change in the code.
    """
    files: dict[str, Path | None] = {
        "settings": settings.config_file,
        "semgrep": semgrep_rules(settings),
        "cspell": cspell_config(settings),
        "vale": vale_config(settings),
        "gitleaks": gitleaks_config(settings),
        "qlty": qlty_config(settings),
        "qlty.configs": first_existing(settings.repo_root, (".qlty/configs",)),
    }
    for t in settings.targets:
        if t.kind != "repo":
            files |= app_inputs(settings, t)
        if t.kind == "python":
            files[f"{t.name}.vulture"] = vulture_config(settings, t)
            files[f"{t.name}.vulture_whitelist"] = vulture_whitelist(settings, t)
            files[f"{t.name}.interrogate"] = interrogate_config(settings, t)
            files[f"{t.name}.contracts"] = import_contracts(settings, t)
    return {
        name: {"file": label(settings, path), "hash": digest(path)}
        for name, path in files.items()
        if path is not None and path.exists()
    }
