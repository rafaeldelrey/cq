# /// script
# requires-python = ">=3.12"
# dependencies = ["radon>=6.0"]
# ///
"""Cyclomatic-complexity gate for production code, measured with radon.

A function may not exceed ``--max`` (default 15, the middle of radon's rank C), with one
exception: functions already above it when the gate was introduced are listed in
``.complexity-baseline.json`` and may stay, but never get worse. That makes the gate a
ratchet: new and changed code is held to the limit, legacy code can only improve.

Usage (from the repo root):
    uv run scripts/complexity_gate.py check apps/agents_server/app
    uv run scripts/complexity_gate.py report apps/agents_server/app --base origin/main
    uv run scripts/complexity_gate.py baseline apps/agents_server/app

``check``    exits 1 when a function is over the limit and not covered by the baseline, got
             worse than its baseline, or improved without the baseline being lowered.
``report``   prints a Markdown table of every function whose complexity changed against
             ``--base`` (for the PR body).
``baseline`` rewrites the baseline for the given paths. Run it only to shrink the file
             after a refactor; growing it needs a reason in the PR (Codex flags it).
"""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

from radon.complexity import cc_rank, cc_visit_ast

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / ".complexity-baseline.json"


def iter_functions(tree: ast.AST, prefix: str = "") -> list[tuple[str, ast.AST]]:
    """Every function in the tree with its qualified name.

    Methods, nested classes, closures and classes inside functions are all included, once
    each. (radon's own ``cc_visit`` lists methods twice and skips nested classes.)
    """
    out: list[tuple[str, ast.AST]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{node.name}"
            out.append((name, node))
            out.extend(iter_functions(node, f"{name}."))
        elif isinstance(node, ast.ClassDef):
            out.extend(iter_functions(node, f"{prefix}{node.name}."))
        else:
            out.extend(iter_functions(node, prefix))
    return out


def function_complexity(node: ast.AST) -> int:
    """radon's cyclomatic complexity for one function, measured on its own."""
    module = ast.Module(body=[node], type_ignores=[])  # type: ignore[list-item]
    return cc_visit_ast(module)[0].complexity


def measure_source(source: str) -> dict[str, int]:
    """Complexity per qualified function name; a repeated name keeps its highest value."""
    result: dict[str, int] = {}
    for name, node in iter_functions(ast.parse(source)):
        result[name] = max(function_complexity(node), result.get(name, 0))
    return result


def python_files(paths: list[str]) -> list[Path]:
    """Every .py file under the given paths, tests excluded."""
    files: list[Path] = []
    for raw in paths:
        path = (REPO_ROOT / raw).resolve()
        candidates = [path] if path.is_file() else sorted(path.rglob("*.py"))
        files.extend(f for f in candidates if "tests" not in f.relative_to(REPO_ROOT).parts)
    return files


def measure_paths(paths: list[str]) -> dict[str, int]:
    """Complexity keyed by ``<repo-relative file>::<qualified name>``."""
    result: dict[str, int] = {}
    for file in python_files(paths):
        rel = file.relative_to(REPO_ROOT).as_posix()
        for name, complexity in measure_source(file.read_text()).items():
            result[f"{rel}::{name}"] = complexity
    return result


def load_baseline() -> dict[str, int]:
    """The grandfathered functions, or an empty baseline when the file does not exist."""
    if not BASELINE_FILE.exists():
        return {}
    return json.loads(BASELINE_FILE.read_text())["functions"]


def in_paths(key: str, paths: list[str]) -> bool:
    """Whether a baseline key belongs to one of the given paths."""
    file = key.split("::", 1)[0]
    return any(file == p.rstrip("/") or file.startswith(p.rstrip("/") + "/") for p in paths)


def verdict(key: str, complexity: int, allowed: int | None, limit: int) -> tuple[str, str] | None:
    """("fail", message) for one function, or None when it is fine."""
    if complexity > limit and allowed is None:
        return (
            "fail",
            f"  {key}: CC {complexity} ({cc_rank(complexity)}) > {limit} — new or renamed function over the limit",
        )
    if allowed is not None and complexity > allowed:
        return "fail", f"  {key}: CC {complexity} > baseline {allowed} — grandfathered function got more complex"
    if allowed is not None and complexity < allowed:
        # A stale allowance lets the function grow back unnoticed: the baseline must follow it down.
        return "fail", f"  {key}: CC {complexity} < baseline {allowed} — improved; lower the baseline (`baseline`)"
    return None


def cmd_check(paths: list[str], limit: int) -> int:
    """Fail on any function over the limit that the baseline does not cover."""
    baseline = load_baseline()
    measured = measure_paths(paths)
    failures = []
    for key, cc in sorted(measured.items()):
        result = verdict(key, cc, baseline.get(key), limit)
        if result:
            failures.append(result[1])
    # An entry whose function is gone would let a new function of that name in unchecked.
    stale = sorted(k for k in baseline if in_paths(k, paths) and k not in measured)
    failures.extend(f"  {k}: in the baseline but no longer exists — remove it (`baseline`)" for k in stale)
    if failures:
        print(f"Complexity gate FAILED (max CC {limit}, radon):")
        print("\n".join(failures))
        print("Split the function, or — for a legacy function only — explain in the PR why it cannot shrink yet.")
        return 1
    legacy = sum(1 for k in baseline if in_paths(k, paths))
    print(f"Complexity gate passed: {len(measured)} functions, max CC {limit}, {legacy} grandfathered.")
    return 0


def git_show(base: str, rel: str) -> str | None:
    """A file's content at ``base``, or None when it did not exist there."""
    proc = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=REPO_ROOT, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def cmd_report(paths: list[str], base: str) -> int:
    """Markdown table of the functions whose complexity changed against ``base``."""
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    rows: list[str] = []
    for rel in diff:
        if not rel.endswith(".py") or "tests" in Path(rel).parts:
            continue
        head_path = REPO_ROOT / rel
        after = measure_source(head_path.read_text()) if head_path.exists() else {}
        old_source = git_show(base, rel)
        before = measure_source(old_source) if old_source else {}
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            fmt = lambda v: "—" if v is None else f"{v} ({cc_rank(v)})"  # noqa: E731
            rows.append(f"| `{rel}::{name}` | {fmt(old)} | {fmt(new)} |")
    if not rows:
        print("No function changed complexity.")
        return 0
    print("| Function | Before | After |\n|---|---|---|")
    print("\n".join(rows))
    return 0


def cmd_baseline(paths: list[str], limit: int) -> int:
    """Rewrite the baseline entries for ``paths`` with every function currently over the limit."""
    kept = {k: v for k, v in load_baseline().items() if not in_paths(k, paths)}
    over = {k: v for k, v in measure_paths(paths).items() if v > limit}
    functions = dict(sorted({**kept, **over}.items()))
    BASELINE_FILE.write_text(json.dumps({"max": limit, "functions": functions}, indent=2) + "\n")
    print(f"Baseline: {len(functions)} grandfathered functions written to {BASELINE_FILE.name}.")
    return 0


def main() -> int:
    """Parse arguments and run one command."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "report", "baseline"])
    parser.add_argument("paths", nargs="+", help="repo-relative directories or files of production code")
    parser.add_argument("--max", type=int, default=15, dest="limit")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    if args.command == "check":
        return cmd_check(args.paths, args.limit)
    if args.command == "report":
        return cmd_report(args.paths, args.base)
    return cmd_baseline(args.paths, args.limit)


if __name__ == "__main__":
    sys.exit(main())
