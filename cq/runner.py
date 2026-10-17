"""Take one snapshot: every collector on every target, at HEAD or at a past commit."""

import importlib.metadata
import json
import shutil
import subprocess
import sys
import time
import tomllib
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cq.collectors import COLLECTORS
from cq.config import HISTORY_DIR, REPO_ROOT, RUNS_DIR, SCHEMA_VERSION, Settings, Target
from cq.context import Context
from cq.proc import log, log_to, run

PINNED_TOOLS = ("radon", "vulture", "interrogate", "complexipy", "semgrep", "pip-audit", "deptry", "import-linter")


def git(*args: str, cwd: Path = REPO_ROOT) -> str:
    """A git command's stdout, stripped."""
    return run(["git", *args], cwd).strip()


# System binaries: recorded rather than pinned (Homebrew upgrades them), with the flag that prints
# the version. qlty is pinned in pyproject and checked by its collectors; recorded here too.
BINARIES = {"betterleaks": "version", "trufflehog": "--version", "vale": "--version", "qlty": "--version"}


def binary_version(tool: str, flag: str) -> str:
    """The installed version line of a system binary, or "not installed"."""
    found = shutil.which(tool)
    if not found:
        return "not installed"
    proc = subprocess.run([found, flag], capture_output=True, text=True, check=False)
    return (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else "unknown"


def qlty_plugin_versions(checkout: Path) -> dict[str, str]:
    """The plugin versions pinned in the checkout's .qlty/qlty.toml (radarlint, zizmor, …)."""
    config = checkout / ".qlty" / "qlty.toml"
    if not config.exists():
        return {}
    plugins = tomllib.loads(config.read_text()).get("plugin", [])
    return {f"qlty:{p['name']}": p["version"] for p in plugins if "version" in p}


def tool_versions(settings: Settings, checkout: Path) -> dict[str, str]:
    """Every measuring tool's version for this snapshot: a change in one explains a step in the trend."""
    pinned = {t: importlib.metadata.version(t) for t in PINNED_TOOLS}
    specs = {
        "jscpd": settings.jscpd, "knip": settings.knip, "mutmut": "3.8.0", "qlty (pinned)": settings.qlty,
        "aislop": settings.aislop, "pyscn": settings.pyscn, "fallow": settings.fallow, "cspell": settings.cspell,
    }  # fmt: skip
    binaries = {tool: binary_version(tool, flag) for tool, flag in BINARIES.items()}
    return pinned | specs | binaries | qlty_plugin_versions(checkout)


def commit_info(rev: str, settings: Settings) -> dict:
    """The commit a snapshot measures, and whether the measured paths had uncommitted changes."""
    sha = git("rev-parse", rev)
    info = {
        "commit": sha,
        "short": sha[:7],
        "commit_date": git("show", "-s", "--format=%cI", sha),
        "subject": git("show", "-s", "--format=%s", sha),
        "dirty": False,
    }
    if rev == "HEAD":
        roots = [t.root for t in settings.targets if t.kind != "repo"]  # the code measured, not the whole repo
        info["dirty"] = bool(git("status", "--porcelain", "--", *roots))
    return info


@contextmanager
def checkout_at(rev: str) -> Iterator[Path]:
    """The repo itself for HEAD; otherwise a temporary detached worktree, removed afterwards."""
    if rev == "HEAD":
        yield REPO_ROOT
        return
    path = RUNS_DIR / "_worktrees" / git("rev-parse", "--short", rev)
    if path.exists():
        git("worktree", "remove", "--force", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--detach", "--force", str(path), rev)
    try:
        yield path
    finally:
        git("worktree", "remove", "--force", str(path))


def prepare(target: Target, checkout: Path) -> None:
    """Install the UI's node_modules in a worktree (the repo's own are assumed installed)."""
    app = target.app_dir(checkout)
    if target.kind == "typescript" and checkout != REPO_ROOT and not (app / "node_modules").exists():
        run(["npm", "ci", "--no-audit", "--no-fund"], app)


class RunLog:
    """Progress lines to stderr and to runs/<run_id>/run.log."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def __call__(self, text: str, end: str = "\n") -> None:
        """Write ``text`` to both."""
        print(text, end=end, flush=True, file=sys.stderr)
        with self.path.open("a") as fh:
            fh.write(text + end)


def run_collector(ctx: Context, tool: str) -> tuple[dict | None, str | None]:
    """One collector's result, or the error that stopped it. Its commands go to <target>/<tool>.log."""
    started = time.monotonic()
    with log_to(ctx.raw_dir / f"{tool}.log"):
        try:
            result = COLLECTORS[tool].collect(ctx)
        except Exception as exc:  # noqa: BLE001 - intentional degraded mode: one tool failing (missing in an
            # old commit, crashed) must not lose the other eleven measurements. The error is stored in the
            # snapshot and shown in the report, and the traceback goes to the tool's log.
            log(f"--- collector failed ---\n{traceback.format_exc()}")
            return None, f"{type(exc).__name__}: {exc}"[:2000]
    result["seconds"] = round(time.monotonic() - started, 2)
    return result, None


def snapshot(
    settings: Settings,
    rev: str = "HEAD",
    targets: list[str] | None = None,
    tools: list[str] | None = None,
    *,
    full: bool = False,
) -> dict:
    """Measure ``rev`` and return the snapshot (not yet saved); ``full`` keeps every item (the gate)."""
    info = commit_info(rev, settings)
    run_at = datetime.now(UTC)
    run_id = f"{run_at:%Y%m%dT%H%M%SZ}_{info['short']}"
    snap: dict = {
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "run_at": run_at.isoformat(timespec="seconds"),
        **info,
        "partial": bool(targets or tools),
        "tool_versions": tool_versions(settings, REPO_ROOT),
        "targets": {},
        "errors": {},
    }
    say = RunLog(RUNS_DIR / run_id / "run.log")
    say(f"run {run_id}: {info['short']} {info['subject'][:80]}")
    with checkout_at(rev) as checkout:
        for target in settings.targets:
            if targets and target.name not in targets:
                continue
            if not target.app_dir(checkout).exists() or not target.source_paths(checkout):
                snap["errors"][target.name] = "not present at this commit"
                continue
            prepare(target, checkout)
            raw_dir = RUNS_DIR / run_id / target.name
            raw_dir.mkdir(parents=True, exist_ok=True)
            ctx = Context(target=target, checkout=checkout, raw_dir=raw_dir, settings=settings, full=full)
            measured: dict = {}
            for tool, module in COLLECTORS.items():
                if target.kind not in module.KINDS or (tools and tool not in tools):
                    continue
                say(f"  {target.name:<17} {tool:<16}", end="")
                result, error = run_collector(ctx, tool)
                if error or result is None:
                    snap["errors"][f"{target.name}.{tool}"] = error
                    say(f"ERROR {(error or '').splitlines()[0][:100]}  (see {target.name}/{tool}.log)")
                else:
                    measured[tool] = result
                    say(f"{result['seconds']:>6.1f}s")
            snap["targets"][target.name] = measured
    return snap


def history_path(snap: dict) -> Path:
    """One file per measured commit: re-measuring a commit replaces its file, so trends stay one point per commit."""
    day = snap["commit_date"][:10]
    suffix = "-dirty" if snap["dirty"] else ""
    return HISTORY_DIR / f"{day}_{snap['short']}{suffix}.json"


def save(snap: dict, *, to_history: bool) -> list[Path]:
    """Write the snapshot under runs/<run_id>/ and, for a complete run, into history/."""
    paths = []
    raw = RUNS_DIR / snap["run_id"] / "snapshot.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(snap, indent=1) + "\n")
    paths.append(raw)
    if to_history:
        HISTORY_DIR.mkdir(exist_ok=True)
        dest = history_path(snap)
        dest.write_text(json.dumps(snap, indent=1) + "\n")
        paths.append(dest)
    return paths


def first_parent_commits(since: str, every_days: int, branch: str = "main") -> list[str]:
    """The last commit on ``branch`` (first parent) before each step from ``since`` to now."""
    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    now = datetime.now(UTC)
    shas: list[str] = []
    step = start
    while step < now:
        sha = git("rev-list", "--first-parent", "-1", f"--before={step.isoformat()}", branch)
        if sha and sha not in shas:
            shas.append(sha)
        step = datetime.fromtimestamp(step.timestamp() + every_days * 86400, UTC)
    return shas


def cleanup_worktrees() -> None:
    """Remove worktrees a crashed run left behind."""
    root = RUNS_DIR / "_worktrees"
    if root.exists():
        shutil.rmtree(root)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT, check=False)
