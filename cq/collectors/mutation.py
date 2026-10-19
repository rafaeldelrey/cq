"""Mutation testing (mutmut): do the tests notice when the code is broken?

mutmut changes the code one small edit at a time (``<`` to ``<=``, a constant, a dropped call)
and runs the tests that cover it. A mutant the tests still pass on "survived": that line runs
under test, but nothing checks what it does. Coverage says the line ran; this says it was tested.

It runs only on the files a target lists in ``mutation_paths``, in a throwaway copy of the app
(mutmut writes a mutants/ tree next to the code), so the app's own folder is never touched.
"""

import re
import shutil
import tempfile
from pathlib import Path

from cq.config import PINS
from cq.context import Context
from cq.proc import run, uv_app
from cq.tally import ranked

KINDS = ("python",)

COPY_IGNORE = shutil.ignore_patterns(
    ".venv", "mutants", "__pycache__", ".pytest_cache", "htmlcov", ".coverage*", "node_modules", ".git", ".cq"
)
RESULT = re.compile(r"^\s*(?P<name>\S+__mutmut_\d+): (?P<status>.+)$")
SHOW_LIMIT = 25


def mutmut(workspace: Path, *args: str) -> list[str]:
    """mutmut overlaid on the app's own dev environment (it needs the app's deps to run its tests)."""
    return uv_app(workspace, "mutmut", *args, overlay=(PINS["mutmut"],))


def configure(workspace: Path, paths: list[str], tests: list[str] | None = None) -> None:
    """Point mutmut at the files to mutate; copy everything else so imports and the files tests read resolve."""
    sources = sorted(p.name for p in workspace.iterdir() if p.name not in {"tests", "mutants", ".venv"})
    table = (
        f"\n\n[tool.mutmut]\npaths_to_mutate = {paths!r}\nalso_copy = {sources!r}\ntests_dir = {tests or ['tests']!r}\n"
    ).replace("'", '"')
    pyproject = workspace / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + table)


def function_of(name: str) -> str:
    """``pkg.module.x_func__mutmut_3`` → ``pkg.module.func`` (classes: ``Cls.xǁCls__method`` style kept readable)."""
    base = name.rsplit("__mutmut_", 1)[0]
    module, _, func = base.rpartition(".")
    return f"{module}.{func.removeprefix('x_').replace('ǁ', '.')}"


def collect(ctx: Context) -> dict:
    """Mutate the configured files, run the tests, count killed and surviving mutants."""
    paths = list(ctx.target.mutation_paths)
    if not paths:
        return {"skipped": "no mutation_paths for this target"}
    with tempfile.TemporaryDirectory(prefix="cq-mutation-") as tmp:
        workspace = Path(tmp) / ctx.target.name
        shutil.copytree(ctx.app_dir, workspace, ignore=COPY_IGNORE)
        configure(workspace, paths, list(ctx.target.mutation_tests))
        log = run(mutmut(workspace, "run", "--max-children", "6"), workspace, ok_codes=(0, 1))
        (ctx.raw_dir / "mutmut-run.txt").write_text(log[-20000:])
        results = run(mutmut(workspace, "results", "--all", "true"), workspace)
        (ctx.raw_dir / "mutmut-results.txt").write_text(results)
        statuses = {m["name"]: m["status"].strip() for m in map(RESULT.match, results.splitlines()) if m}
        survivors = [n for n, s in statuses.items() if s == "survived"]
        diffs = {n: run(mutmut(workspace, "show", n), workspace).strip() for n in survivors[:SHOW_LIMIT]}
    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    killed = counts.get("killed", 0)
    judged = killed + counts.get("survived", 0)
    by_function = ranked(function_of(n) for n in survivors)
    return {
        "paths": paths,
        "mutants": len(statuses),
        "killed": killed,
        "survived": counts.get("survived", 0),
        "score": round(100 * killed / judged, 2) if judged else None,
        "by_status": counts,
        "by_function": by_function,
        "items": [{"mutant": n, "function": function_of(n), "diff": diffs.get(n, "")[-1200:]} for n in survivors[:80]],
    }
