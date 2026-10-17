"""Where cq measures and how: the repo, its settings, and the tool versions this release pins.

The settings come from the measured repo: ``cq.toml`` at its root, else ``[tool.cq]`` in its
``pyproject.toml``, else nothing, and the targets are detected. ``cq.toml`` holds the same keys as
``[tool.cq]``, at its top level.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
CONFIG_FILE = "cq.toml"
DEFAULT_DATA_DIR = ".cq"
SCHEMA_VERSION = 1

# The measuring tools that run outside cq's own environment, pinned by the cq release so a
# snapshot stays comparable with the last. Bumping one is a measurement change: the trend shows a
# step there, and the snapshot's tool_versions say why. cq's Python tools are pinned in its
# pyproject; the system binaries are recorded, not pinned.
PINS = {
    "jscpd": "jscpd@4.0.5",
    "knip": "knip@6.41.0",
    "qlty": "0.654.0",
    "aislop": "aislop@0.18.1",
    "pyscn": "pyscn==1.32.4",
    "fallow": "fallow@3.33.1",
    "cspell": "cspell@10.3.6",
    "mutmut": "mutmut==3.8.0",
    "deptry": "deptry==0.25.1",
}


@dataclass(frozen=True)
class Target:
    """One app to measure: where it lives and what its production code is."""

    name: str
    kind: str  # "python" | "typescript" | "repo"
    root: str  # repo-relative app directory; "." is the repo root
    source: tuple[str, ...]  # production paths, relative to root
    tests: bool
    former_roots: tuple[str, ...] = ()  # where the app lived in older commits
    mutation_paths: tuple[str, ...] = ()  # files mutation testing mutates, relative to root
    mutation_tests: tuple[str, ...] = ()  # the tests mutmut runs (default: all of them)
    deptry_ignore: tuple[str, ...] = ()  # "DEP002=uvicorn": a deptry finding that is wrong for this app
    image: str = ""  # the published image Trivy scans

    def root_in(self, checkout: Path) -> str:
        """The app's repo-relative directory in this checkout: today's, or the first former one present."""
        for root in (self.root, *self.former_roots):
            if (checkout / root).is_dir():
                return root
        return self.root

    def app_dir(self, checkout: Path) -> Path:
        """The app directory inside a checkout (the repo, or a worktree at another commit)."""
        return checkout / self.root_in(checkout)

    def source_paths(self, checkout: Path) -> list[Path]:
        """Production paths that exist in this checkout."""
        return [p for p in (self.app_dir(checkout) / s for s in self.source) if p.exists()]

    def repo_path(self, rel: str) -> str:
        """A path inside the app, repo-relative ("uv.lock" → "apps/x/uv.lock", or "uv.lock" at the root)."""
        return rel if self.root in (".", "") else f"{self.root}/{rel}"

    def owns(self, path: str) -> bool:
        """Whether a repo-relative path is inside the app (every path, for an app at the root)."""
        return self.root in (".", "") or path.startswith(f"{self.root}/")


REPO_TARGET = Target(name="repo", kind="repo", root=".", source=(".",), tests=False)


@dataclass(frozen=True)
class Settings:
    """The measured repo and its cq settings."""

    targets: tuple[Target, ...]
    jscpd: str = PINS["jscpd"]
    knip: str = PINS["knip"]
    complexity_limit: int = 15
    cognitive_limit: int = 15
    shell_roots: tuple[str, ...] = ()  # where the tracked *.sh files shellcheck checks live
    link_paths: tuple[str, ...] = ()  # docs lychee and vale check
    link_exclude: tuple[str, ...] = ()  # paths lychee and vale skip (frozen history)
    qlty: str = PINS["qlty"]
    aislop: str = PINS["aislop"]
    pyscn: str = PINS["pyscn"]
    fallow: str = PINS["fallow"]
    cspell: str = PINS["cspell"]
    repo_root: Path = Path()  # the measured repo
    data_dir: Path = Path(DEFAULT_DATA_DIR)  # history/ and runs/
    config_dir: Path = Path()  # relative paths in the settings resolve from here
    config_file: Path | None = None  # where the settings came from; None when detected
    # The repo's own files for the tools, relative to config_dir; empty means "look in the repo,
    # then use cq's default" (cq/toolconfig.py).
    semgrep_rules: str = ""
    contracts_dir: str = ""
    cspell_config: str = ""
    vale_config: str = ""
    gitleaks_config: str = ""
    vulture_config: str = ""  # a toml holding [tool.vulture], for every Python app
    interrogate_config: str = ""  # a toml holding [tool.interrogate], for every Python app
    complexity_baseline: str = ".complexity-baseline.json"  # relative to the repo root
    name: str = ""  # the dashboard's title: "<name> code quality"; the repo folder's name by default

    @property
    def history_dir(self) -> Path:
        """One snapshot per measured commit: the trend."""
        return self.data_dir / "history"

    @property
    def runs_dir(self) -> Path:
        """One folder per run: snapshot, report, dashboard and each tool's raw output."""
        return self.data_dir / "runs"


@dataclass(frozen=True)
class GateSettings:
    """The gate table: how the PR gate judges a branch."""

    mode: Literal["warn", "block"] = "warn"  # "warn": report and exit 0; "block": exit 1 on a blocking finding
    block: tuple[str, ...] = ()  # "tool:rule" patterns that block, on top of every error


def find_config(repo_root: Path, config: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    """The settings table and the file it came from: ``config``, cq.toml, or pyproject's [tool.cq]."""
    if config is not None:
        data = tomllib.loads(config.read_text())
        return (data.get("tool", {}).get("cq", {}) if config.name == "pyproject.toml" else data), config
    own = repo_root / CONFIG_FILE
    if own.exists():
        return tomllib.loads(own.read_text()), own
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        table = tomllib.loads(pyproject.read_text()).get("tool", {}).get("cq")
        if table is not None:
            return table, pyproject
    return {}, None


def has_python(folder: Path) -> bool:
    """Whether a folder holds any .py file, at any depth."""
    return folder.is_dir() and next(folder.rglob("*.py"), None) is not None


def python_source(repo_root: Path, project_name: str) -> tuple[str, ...]:
    """A root project's production code: src/, else its package, else its top-level packages,
    else its top-level modules; never the whole root, which holds .venv and other installed code.

    src/ and the project's package count only when they hold Python, and the package may be a
    namespace package (no __init__.py). Other folders need an __init__.py, so scripts/ or docs/
    are never taken for production code.
    """
    if has_python(repo_root / "src"):
        return ("src",)
    package = re.sub(r"[-.]+", "_", project_name).lower()
    if has_python(repo_root / package):
        return (package,)
    packages = sorted(
        p.name
        for p in repo_root.iterdir()
        if p.is_dir() and (p / "__init__.py").exists() and p.name not in ("tests", "test")
    )
    modules = sorted(
        p.name
        for p in repo_root.glob("*.py")
        if not p.name.startswith("test_") and p.name not in ("conftest.py", "setup.py", "noxfile.py")
    )
    return tuple(packages) or tuple(modules)


def detect_targets(repo_root: Path) -> tuple[Target, ...]:
    """The targets of a repo with no settings: a pyproject.toml at the root is one Python app."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return (REPO_TARGET,)
    name = tomllib.loads(pyproject.read_text()).get("project", {}).get("name") or repo_root.name
    source = python_source(repo_root, name)
    if not source:  # a pyproject with no Python code beside it (a tool config, a docs site)
        return (REPO_TARGET,)
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    app = Target(
        name=slug if slug and slug != REPO_TARGET.name else "app",  # "repo" is the repo target's name
        kind="python",
        root=".",
        source=source,
        tests=(repo_root / "tests").is_dir(),
    )
    return (app, REPO_TARGET)


KINDS = ("python", "typescript", "repo")


def parse_target(t: dict[str, Any]) -> Target:
    """One [[targets]] entry; an unknown kind is a configuration error, not an app nothing measures."""
    if t["kind"] not in KINDS:
        raise ValueError(f"target {t['name']!r}: kind must be one of {', '.join(KINDS)}, not {t['kind']!r}")
    return Target(
        name=t["name"],
        kind=t["kind"],
        root=t["root"],
        source=tuple(t["source"]),
        tests=bool(t.get("tests", False)),
        former_roots=tuple(t.get("former_roots", [])),
        mutation_paths=tuple(t.get("mutation_paths", [])),
        mutation_tests=tuple(t.get("mutation_tests", [])),
        deptry_ignore=tuple(t.get("deptry_ignore", [])),
        image=str(t.get("image", "")),
    )


def default_docs(repo_root: Path) -> list[str]:
    """The docs to check when the settings name none: a README and a docs/ folder, when present."""
    return [p for p in ("README.md", "docs") if (repo_root / p).exists()]


def load_settings(repo_root: Path | None = None, config: Path | None = None) -> Settings:
    """The measured repo's settings, with its targets listed or detected."""
    root = (repo_root or Path.cwd()).resolve()
    table, source = find_config(root, config.resolve() if config else None)
    config_dir = source.parent if source else root
    targets = tuple(parse_target(t) for t in table["targets"]) if table.get("targets") else detect_targets(root)
    if not any(t.kind == "repo" for t in targets):
        targets = (*targets, REPO_TARGET)  # secrets, scripts, links and vale run on the repo target
    names = [t.name for t in targets]
    if len(set(names)) != len(names):  # a snapshot keys results by name: a duplicate would overwrite
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"target names must be unique; repeated: {', '.join(dupes)} ('repo' is the repo target's)")
    return Settings(
        targets=targets,
        complexity_limit=int(table.get("complexity_limit", 15)),
        cognitive_limit=int(table.get("cognitive_limit", 15)),
        shell_roots=tuple(table.get("shell_roots", ["."])),
        link_paths=tuple(table.get("link_paths", default_docs(root))),
        link_exclude=tuple(table.get("link_exclude", [])),
        repo_root=root,
        data_dir=(config_dir / table.get("data_dir", DEFAULT_DATA_DIR)).resolve(),
        config_dir=config_dir,
        config_file=source,
        semgrep_rules=str(table.get("semgrep_rules", "")),
        contracts_dir=str(table.get("contracts_dir", "")),
        cspell_config=str(table.get("cspell_config", "")),
        vale_config=str(table.get("vale_config", "")),
        gitleaks_config=str(table.get("gitleaks_config", "")),
        vulture_config=str(table.get("vulture_config", "")),
        interrogate_config=str(table.get("interrogate_config", "")),
        complexity_baseline=str(table.get("complexity_baseline", ".complexity-baseline.json")),
        name=str(table.get("name", root.name)),
    )


def load_gate(settings: Settings) -> GateSettings:
    """The gate table of the repo's settings; an unknown mode is a configuration error."""
    table, _ = find_config(settings.repo_root, settings.config_file)
    gate = table.get("gate", {})
    mode = gate.get("mode", "warn")
    if mode not in ("warn", "block"):
        raise ValueError(f"gate mode must be warn or block, not {mode!r}")
    return GateSettings(mode=mode, block=tuple(gate.get("block", [])))


def ensure_data_dir(settings: Settings) -> None:
    """Make sure git never sees what cq writes: history/ and runs/ are ignored.

    A new data folder ignores itself whole. An existing one (pre-created, or a tracked folder that
    also holds the settings) gets the missing ``history/`` and ``runs/`` lines added to its own
    .gitignore, whose other rules stay as they are.
    """
    ignore = settings.data_dir / ".gitignore"
    if not settings.data_dir.exists():
        settings.data_dir.mkdir(parents=True)
        ignore.write_text("# cq's history and runs: never committed\n*\n")
        return
    rules = {line.strip().strip("/") for line in ignore.read_text().splitlines()} if ignore.exists() else set()
    missing = [f"{name}/" for name in ("history", "runs") if not rules & {"*", name}]
    if missing:
        text = ignore.read_text() if ignore.exists() else ""
        prefix = "" if not text or text.endswith("\n") else "\n"
        ignore.write_text(text + prefix + "# cq's history and runs: never committed\n" + "\n".join(missing) + "\n")
