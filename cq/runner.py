"""Take one snapshot: every collector on every target, at HEAD or at a past commit."""

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cq.collectors import COLLECTORS
from cq.config import PINS, QLTY_CACHE, QLTY_DIR, SCHEMA_VERSION, Settings, Target, ensure_data_dir
from cq.context import Context
from cq.proc import ToolError, log, log_to, run
from cq.toolconfig import used_configs

PINNED_TOOLS = ("radon", "vulture", "interrogate", "complexipy", "semgrep", "pip-audit", "deptry", "import-linter")


def git(*args: str, cwd: Path | None = None) -> str:
    """A git command's stdout, stripped; in the current directory (the measured repo) by default."""
    return run(["git", *args], cwd or Path.cwd()).strip()


# System binaries: recorded rather than pinned (Homebrew upgrades them), with the flag that prints
# the version. qlty is pinned by the cq release and checked by its collectors; recorded here too.
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
    pinned = {"cq": importlib.metadata.version("cq")} | {t: importlib.metadata.version(t) for t in PINNED_TOOLS}
    specs = {
        "jscpd": settings.jscpd,
        "knip": settings.knip,
        "mutmut": PINS["mutmut"],
        "qlty (pinned)": settings.qlty,
        "aislop": settings.aislop,
        "pyscn": settings.pyscn,
        "fallow": settings.fallow,
        "cspell": settings.cspell,
    }
    binaries = {tool: binary_version(tool, flag) for tool, flag in BINARIES.items()}
    return pinned | specs | binaries | qlty_plugin_versions(checkout)


def commit_info(rev: str, settings: Settings) -> dict:
    """The commit a snapshot measures, and whether the measured paths had uncommitted changes."""
    root = settings.repo_root
    sha = git("rev-parse", rev, cwd=root)
    info = {
        "commit": sha,
        "short": sha[:7],
        "commit_date": git("show", "-s", "--format=%cI", sha, cwd=root),
        "subject": git("show", "-s", "--format=%s", sha, cwd=root),
        "dirty": False,
    }
    if rev == "HEAD":
        # The whole tree: the repo target measures root files too (docs, scripts, workflows, secrets).
        # cq's own output never makes it dirty, wherever its data folder is, and neither does the cache
        # qlty writes under .qlty/ when cq runs it (a repo without qlty's .gitignore would show it).
        produced = [
            f":(exclude){p.relative_to(root)}"
            for p in (settings.history_dir, settings.runs_dir)
            if p.is_relative_to(root)
        ] + [f":(exclude){QLTY_DIR}/{entry}" for entry in QLTY_CACHE]
        info["dirty"] = bool(git("status", "--porcelain", "--", ".", *produced, cwd=root))
    return info


def worktrees_dir(settings: Settings) -> Path:
    """Where old commits are checked out: outside the repo, so no tool run at its root walks into them."""
    key = hashlib.sha256(str(settings.repo_root).encode()).hexdigest()[:10]
    # Resolved: macOS's temp folder is behind a symlink (/var → /private/var), and tool output names
    # the resolved path, which must match the checkout's for paths to come out repo-relative.
    return Path(tempfile.gettempdir()).resolve() / "cq-worktrees" / key


@contextmanager
def checkout_at(rev: str, settings: Settings) -> Iterator[Path]:
    """The repo itself for HEAD; otherwise a temporary detached worktree, removed afterwards."""
    root = settings.repo_root
    if rev == "HEAD":
        yield root
        return
    path = worktrees_dir(settings) / git("rev-parse", "--short", rev, cwd=root)
    if path.exists():
        git("worktree", "remove", "--force", str(path), cwd=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--detach", "--force", str(path), rev, cwd=root)
    try:
        yield path
    finally:
        git("worktree", "remove", "--force", str(path), cwd=root)


# The frozen install for each lockfile; an app with none gets a plain npm install.
INSTALLS = (
    ("pnpm-lock.yaml", ["pnpm", "install", "--frozen-lockfile"]),
    ("yarn.lock", ["yarn", "install", "--frozen-lockfile"]),
    ("package-lock.json", ["npm", "ci", "--no-audit", "--no-fund"]),
)


def install_command(app: Path) -> list[str]:
    """The package manager's frozen install for this app, chosen by its lockfile."""
    return next((cmd for lock, cmd in INSTALLS if (app / lock).exists()), ["npm", "install", "--no-audit", "--no-fund"])


def prepare(target: Target, checkout: Path, settings: Settings) -> str | None:
    """Install the UI's node_modules in a worktree (the repo's own are assumed installed).

    Returns the error when the install fails, so that target is recorded as not measured and the
    other targets still are.
    """
    app = target.app_dir(checkout)
    if target.kind != "typescript" or checkout == settings.repo_root or (app / "node_modules").exists():
        return None
    try:
        run(install_command(app), app)
    except ToolError as exc:  # intentional degraded mode: one app's install must not lose the others
        return f"dependency install failed: {exc}"[:2000]
    return None


class RunLog:
    """Progress lines to stderr and to runs/<run_id>/run.log."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def __call__(self, text: str, end: str = "\n") -> None:
        """Write ``text`` to both."""
        # aislop-ignore-next-line ai-slop/python-print-debug -- RunLog is the run's progress on stderr
        print(text, end=end, flush=True, file=sys.stderr)  # nosemgrep: cq-no-print - RunLog is the run's progress
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


def target_problem(target: Target, checkout: Path, settings: Settings) -> str | None:
    """Why a target can't be measured at this checkout (absent, or its install failed), or None."""
    if not target.app_dir(checkout).exists() or not target.source_paths(checkout):
        return "not present at this commit"
    return prepare(target, checkout, settings)


def measure_target(ctx: Context, tools: list[str] | None, say: RunLog) -> tuple[dict, dict[str, str]]:
    """Every collector that applies to the target: the results by tool, and the errors as ``<target>.<tool>``."""
    name = ctx.target.name
    measured: dict = {}
    errors: dict[str, str] = {}
    for tool, module in COLLECTORS.items():
        if ctx.target.kind not in module.KINDS or (tools and tool not in tools):
            continue
        say(f"  {name:<17} {tool:<16}", end="")
        result, error = run_collector(ctx, tool)
        if error or result is None:
            errors[f"{name}.{tool}"] = error or "the collector returned no result"
            say(f"ERROR {errors[f'{name}.{tool}'].splitlines()[0][:100]}  (see {name}/{tool}.log)")
        else:
            measured[tool] = result
            say(f"{result['seconds']:>6.1f}s")
    return measured, errors


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
        "tool_versions": tool_versions(settings, settings.repo_root),
        "configs": used_configs(settings),
        "targets": {},
        "errors": {},
    }
    ensure_data_dir(settings)
    say = RunLog(settings.runs_dir / run_id / "run.log")
    say(f"run {run_id}: {info['short']} {info['subject'][:80]}")
    with checkout_at(rev, settings) as checkout:
        for target in settings.targets:
            if targets and target.name not in targets:
                continue
            if unmeasurable := target_problem(target, checkout, settings):
                snap["errors"][target.name] = unmeasurable
                continue
            raw_dir = settings.runs_dir / run_id / target.name
            raw_dir.mkdir(parents=True, exist_ok=True)
            ctx = Context(target=target, checkout=checkout, raw_dir=raw_dir, settings=settings, full=full)
            snap["targets"][target.name], errors = measure_target(ctx, tools, say)
            snap["errors"] |= errors
    return snap


def history_path(snap: dict, settings: Settings) -> Path:
    """One file per measured commit: re-measuring a commit replaces its file, so trends stay one point per commit."""
    day = snap["commit_date"][:10]
    suffix = "-dirty" if snap["dirty"] else ""
    return settings.history_dir / f"{day}_{snap['short']}{suffix}.json"


def save(snap: dict, settings: Settings, *, to_history: bool) -> list[Path]:
    """Write the snapshot under runs/<run_id>/ and, for a complete run, into history/."""
    paths = []
    raw = settings.runs_dir / snap["run_id"] / "snapshot.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(snap, indent=1) + "\n")
    paths.append(raw)
    if to_history:
        settings.history_dir.mkdir(parents=True, exist_ok=True)
        dest = history_path(snap, settings)
        dest.write_text(json.dumps(snap, indent=1) + "\n")
        paths.append(dest)
    return paths


def first_parent_commits(since: str, every_days: int, cwd: Path, branch: str = "HEAD") -> list[str]:
    """The last commit on ``branch`` (first parent) before each step from ``since`` to now."""
    if every_days < 1:
        raise ValueError(f"backfill steps must be at least a day apart, not {every_days}")
    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    now = datetime.now(UTC)
    shas: list[str] = []
    step = start
    while step < now:
        sha = git("rev-list", "--first-parent", "-1", f"--before={step.isoformat()}", branch, cwd=cwd)
        if sha and sha not in shas:
            shas.append(sha)
        step = datetime.fromtimestamp(step.timestamp() + every_days * 86400, UTC)
    return shas


def cleanup_worktrees(settings: Settings) -> None:
    """Remove worktrees a crashed run left behind."""
    root = worktrees_dir(settings)
    if root.exists():
        shutil.rmtree(root)
    subprocess.run(["git", "worktree", "prune"], cwd=settings.repo_root, check=False)
