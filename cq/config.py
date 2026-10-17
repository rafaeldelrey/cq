"""Paths and the measured targets, read from this project's pyproject.toml."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_DIR.parent
PYPROJECT = PROJECT_DIR / "pyproject.toml"
HISTORY_DIR = PROJECT_DIR / "history"
RUNS_DIR = PROJECT_DIR / "runs"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Target:
    """One app to measure: where it lives and what its production code is."""

    name: str
    kind: str  # "python" | "typescript" | "repo"
    root: str  # repo-relative app directory
    source: tuple[str, ...]  # production paths, relative to root
    tests: bool
    former_roots: tuple[str, ...] = ()  # where the app lived in older commits
    mutation_paths: tuple[str, ...] = ()  # files mutation testing mutates, relative to root
    mutation_tests: tuple[str, ...] = ()  # the tests mutmut runs (default: all of them)
    deptry_ignore: tuple[str, ...] = ()  # "DEP002=uvicorn": a deptry finding that is wrong for this app
    image: str = ""  # the published image Trivy scans (what build-and-push.sh pushes)

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


@dataclass(frozen=True)
class Settings:
    """The [tool.code_quality] table."""

    targets: tuple[Target, ...]
    jscpd: str
    knip: str
    complexity_limit: int
    cognitive_limit: int
    shell_roots: tuple[str, ...] = ()  # where the tracked *.sh files shellcheck checks live
    link_paths: tuple[str, ...] = ()  # docs lychee checks for broken links
    link_exclude: tuple[str, ...] = ()  # paths lychee skips (frozen history)
    qlty: str = ""  # the pinned qlty CLI version
    aislop: str = ""  # npx package spec, e.g. "aislop@0.18.1"
    pyscn: str = ""  # uvx package spec, e.g. "pyscn==1.32.4"
    fallow: str = ""  # npx package spec
    cspell: str = ""  # npx package spec


@dataclass(frozen=True)
class GateSettings:
    """The [tool.code_quality.gate] table: how the PR gate judges a branch."""

    mode: Literal["warn", "block"] = "warn"  # "warn": report and exit 0; "block": exit 1 on a blocking finding
    block: tuple[str, ...] = ()  # "tool:rule" patterns that block, on top of every error


def load_gate(pyproject: Path = PYPROJECT) -> GateSettings:
    """Parse [tool.code_quality.gate]; an unknown mode is a configuration error."""
    table = tomllib.loads(pyproject.read_text())["tool"]["code_quality"].get("gate", {})
    mode = table.get("mode", "warn")
    if mode not in ("warn", "block"):
        raise ValueError(f"[tool.code_quality.gate] mode must be warn or block, not {mode!r}")
    return GateSettings(mode=mode, block=tuple(table.get("block", [])))


def load_settings(pyproject: Path = PYPROJECT) -> Settings:
    """Parse [tool.code_quality] from pyproject.toml."""
    table = tomllib.loads(pyproject.read_text())["tool"]["code_quality"]
    targets = tuple(
        Target(
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
        for t in table["targets"]
    )
    return Settings(
        targets=targets,
        jscpd=table["jscpd"],
        knip=table["knip"],
        complexity_limit=int(table["complexity_limit"]),
        cognitive_limit=int(table["cognitive_limit"]),
        shell_roots=tuple(table.get("shell_roots", [])),
        link_paths=tuple(table.get("link_paths", [])),
        link_exclude=tuple(table.get("link_exclude", [])),
        qlty=str(table.get("qlty", "")),
        aislop=str(table.get("aislop", "")),
        pyscn=str(table.get("pyscn", "")),
        fallow=str(table.get("fallow", "")),
        cspell=str(table.get("cspell", "")),
    )
