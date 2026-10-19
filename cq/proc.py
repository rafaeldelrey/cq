"""Running the measuring tools as subprocesses."""

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

# The log file of the tool being run: every command, its exit code, timing and stderr go there.
_log_file: ContextVar[Path | None] = ContextVar("log_file", default=None)


class ToolError(RuntimeError):
    """A tool could not produce a measurement (crashed, missing, unparsable output)."""


def run(
    cmd: list[str],
    cwd: Path,
    *,
    ok_codes: tuple[int, ...] = (0,),
    env: dict[str, str] | None = None,
    secret_output: bool = False,
) -> str:
    """Run ``cmd`` and return its stdout; raise ToolError on an exit code outside ``ok_codes``.

    Linters exit non-zero when they find something, so callers list the codes that still mean
    "measured". ``VIRTUAL_ENV`` is dropped so ``uv run`` inside an app uses the app's own venv.
    ``secret_output``: the tool prints secret values (trufflehog, betterleaks), so neither its
    stdout nor its stderr reaches the tool's log or an error message; only their lengths are.
    """
    full_env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    full_env.update(env or {})
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=full_env, check=False)
    if secret_output:
        logged_stdout = f"<{len(proc.stdout)} chars withheld: may hold secret values>"
        logged_stderr = f"<{len(proc.stderr)} chars withheld: may hold secret values>"
    else:
        logged_stdout, logged_stderr = proc.stdout.strip()[-3000:], proc.stderr.strip()
    log(
        f"$ {' '.join(cmd)}\n  cwd {cwd}\n  exit {proc.returncode} in {time.monotonic() - started:.1f}s\n"
        f"--- stderr ---\n{logged_stderr}\n--- stdout (last 3000 chars) ---\n{logged_stdout}\n"
    )
    if proc.returncode not in ok_codes:
        tail = (
            "(output withheld: may hold secret values)"
            if secret_output
            else (proc.stderr or proc.stdout).strip()[-1500:]
        )
        raise ToolError(f"{' '.join(cmd)} exited {proc.returncode}: {tail}")
    return proc.stdout


def own(tool: str) -> str:
    """A tool cq installs with itself (vulture, semgrep, …), from cq's own environment.

    ``uv tool install`` puts only the ``cq`` command on the PATH, not its dependencies' scripts, so
    they are found next to the interpreter running cq; the bare name is the fallback.
    """
    path = Path(sys.executable).parent / tool
    return str(path) if path.exists() else tool


def require_node_modules(app_dir: Path) -> None:
    """The npm tools run from the app's own install: refuse without it, rather than measure nothing."""
    if not (app_dir / "node_modules").exists():
        raise ToolError("node_modules missing; run `npm ci` in the app first")


def require(*tools: str) -> None:
    """A missing binary is a blind spot, never a clean result: fail with how to install it."""
    for tool in tools:
        if shutil.which(tool) is None:
            raise ToolError(f"{tool} is not installed")


def parse_json(out: str, tool: str) -> Any:
    """A tool's JSON output, or a ToolError quoting the end of what it printed instead."""
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise ToolError(f"{tool} printed no JSON: {out[-300:]}") from exc


@contextmanager
def log_to(path: Path) -> Iterator[None]:
    """Send the commands run inside the block to ``path`` (appended)."""
    token = _log_file.set(path)
    try:
        yield
    finally:
        _log_file.reset(token)


def log(text: str) -> None:
    """Append to the current tool's log, if there is one."""
    path = _log_file.get()
    if path is not None:
        with path.open("a") as fh:
            fh.write(text.rstrip("\n") + "\n\n")


def has_dev_extra(app_dir: Path) -> bool:
    """Whether the app declares a ``dev`` extra (optional-dependencies) rather than a dev group."""
    pyproject = app_dir / "pyproject.toml"
    if not pyproject.exists():
        return False
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- a pyproject may have no [project] table or optional dependencies
    return "dev" in tomllib.loads(pyproject.read_text()).get("project", {}).get("optional-dependencies", {})


def uv_app(app_dir: Path, *args: str, overlay: tuple[str, ...] = ()) -> list[str]:
    """A command run in an app's own environment, with its dev tools.

    The dev tools are a ``dev`` extra in older apps, or a ``dev`` dependency group, which
    ``uv run`` includes by default. ``overlay`` adds packages on top with ``uv run --with``, for
    old commits whose dev tools did not have the tool yet.
    """
    extra = ["--extra", "dev"] if has_dev_extra(app_dir) else []
    with_args = [arg for pkg in overlay for arg in ("--with", pkg)]
    return ["uv", "run", "-q", *extra, *with_args, *args]


def run_app_tool(
    packages: tuple[str, ...],
    args: list[str],
    cwd: Path,
    *,
    ok_codes: tuple[int, ...] = (0,),
    env: dict[str, str] | None = None,
    overlay: tuple[str, ...] = (),
) -> str:
    """Run a tool in the app's environment; when the app does not have it, overlay it and retry.

    An app with the tool among its dev tools runs its own pinned version; the retry is for an app,
    or an old commit, without it.
    """
    try:
        return run(uv_app(cwd, *args, overlay=overlay), cwd, ok_codes=ok_codes, env=env)
    except ToolError as exc:
        if "Failed to spawn" not in str(exc) and "extra" not in str(exc).lower():
            raise
    with_args = [arg for pkg in (*packages, *overlay) for arg in ("--with", pkg)]
    # An app without its own pyproject would otherwise resolve the repo root's project.
    isolate = [] if (cwd / "pyproject.toml").exists() else ["--no-project"]
    return run(["uv", "run", "-q", *isolate, *with_args, *args], cwd, ok_codes=ok_codes, env=env)
