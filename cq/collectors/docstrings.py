"""Docstring coverage (interrogate), with this project's [tool.interrogate] config.

code-standards.md asks for docstrings on functions; this tracks how much of the code has them,
and lists each function, class or module without one (``items``), with the lines it spans up to
the next symbol, so the PR gate can tell a changed function from an untouched one.
"""

import ast
import re
from collections.abc import Callable
from pathlib import Path

from cq.collectors.size import python_files
from cq.config import PYPROJECT
from cq.context import Context
from cq.proc import ToolError, run

KINDS = ("python",)

TOTAL = re.compile(r"\|\s*TOTAL\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)%")
# The detailed table: "| path.py (module) |  MISSED |", then "| Class.method (L12) | COVERED |".
DETAIL_ROW = re.compile(r"^\|\s*(?P<name>.+?)\s+\((?:module|L(?P<line>\d+))\)\s*\|\s*(?P<status>MISSED|COVERED)\s*\|")
FILE_ROW = re.compile(r"\|\s*(\S+\.py)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)%")


def syntax_ends(path: Path) -> dict[int, tuple[int, int]]:
    """Each def and class in a file, by its ``def``/``class`` line (interrogate's), mapped to the
    lines it spans: from its first decorator to its last line (Python's own syntax boundaries,
    nested symbols included)."""
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (OSError, SyntaxError, ValueError):  # degraded mode: a file that can't be read or parsed
        # gives its symbols a one-line span (their own line), so only an edit on that line counts.
        return {}
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return {
        n.lineno: (min([d.lineno for d in n.decorator_list] + [n.lineno]), n.end_lineno or n.lineno)
        for n in ast.walk(tree)
        if isinstance(n, kinds)
    }


def missing(out: str, full: Callable[[str], str], ends: Callable[[str], dict[int, tuple[int, int]]]) -> list[dict]:
    """The symbols interrogate marks MISSED, each with its file and the lines it spans, from the
    syntax tree (``ends``: def line → first decorator line, last line). A module has no span
    (``end_line`` None): it covers the whole file."""
    rows: list[dict] = []
    file = ""
    for line in out.splitlines():
        m = DETAIL_ROW.match(line)
        if not m:
            continue
        if m["line"] is None:  # "(module)": a new file section
            file = full(m["name"])
            rows.append({"file": file, "symbol": "(module)", "line": 1, "missed": m["status"] == "MISSED"})
        else:
            rows.append({"file": file, "symbol": m["name"], "line": int(m["line"]), "missed": m["status"] == "MISSED"})
    items = []
    spans: dict[str, dict[int, tuple[int, int]]] = {}
    for row in rows:
        if not row["missed"]:
            continue
        start, end = row["line"], None
        if row["symbol"] != "(module)":
            if row["file"] not in spans:
                spans[row["file"]] = ends(row["file"])
            start, end = spans[row["file"]].get(row["line"], (row["line"], row["line"]))
        items.append(
            {"file": row["file"], "symbol": row["symbol"], "line": row["line"], "start_line": start, "end_line": end}
        )
    return items


def worst_first(items: list[dict]) -> list[dict]:
    """The files with the most gaps first, each in line order, so a report cap keeps whole files
    from all over the app instead of the first paths alphabetically."""
    per_file: dict[str, int] = {}
    for i in items:
        per_file[i["file"]] = per_file.get(i["file"], 0) + 1
    return sorted(items, key=lambda i: (-per_file[i["file"]], i["file"], i["line"]))


def collect(ctx: Context) -> dict:
    """interrogate -vv prints a per-file table and a TOTAL row (total, missed, covered, %)."""
    paths = [str(p) for p in ctx.sources]
    out = run(["interrogate", "-vv", "--config", str(PYPROJECT), "--no-color", *paths], ctx.checkout, ok_codes=(0, 1))
    (ctx.raw_dir / "interrogate.txt").write_text(out)
    m = TOTAL.search(out)
    if not m:
        raise ToolError(f"no TOTAL row in interrogate output: {out[-500:]}")
    total, missed, covered, percent = int(m[1]), int(m[2]), int(m[3]), float(m[4])
    # interrogate names files relative to the deepest folder they share; match them back by suffix.
    known = [ctx.rel(f) for f in python_files(ctx.sources)]

    def full(name: str) -> str:
        return next((k for k in known if k == name or k.endswith("/" + name)), name)

    files = [
        {"file": full(name), "missing": int(miss), "percent": float(pct)}
        for name, _, miss, _, pct in FILE_ROW.findall(out)
        if int(miss) > 0
    ]
    files.sort(key=lambda f: -f["missing"])
    return {
        "percent": percent,
        "total": total,
        "missing": missed,
        "covered": covered,
        "worst_files": files[:15],
        "items": ctx.cap(worst_first(missing(out, full, lambda f: syntax_ends(ctx.checkout / f))), 200),
    }
