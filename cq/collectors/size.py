"""Size of the production code: files, lines, logical lines, comments (radon raw)."""

from pathlib import Path

from radon.raw import analyze

from cq.context import Context

KINDS = ("python",)


def python_files(paths: list[Path]) -> list[Path]:
    """Every .py file under the given paths, test directories excluded."""
    files: list[Path] = []
    for path in paths:
        candidates = [path] if path.is_file() else sorted(path.rglob("*.py"))
        files.extend(f for f in candidates if "tests" not in f.parts and f.suffix == ".py")
    return files


def collect(ctx: Context) -> dict:
    """Totals over the target's production files."""
    totals: dict[str, int] = {"files": 0, "loc": 0, "sloc": 0, "lloc": 0, "comments": 0, "blank": 0}
    for file in python_files(ctx.sources):
        raw = analyze(file.read_text())
        totals["files"] += 1
        totals["loc"] += raw.loc
        totals["sloc"] += raw.sloc
        totals["lloc"] += raw.lloc
        totals["comments"] += raw.comments + raw.multi
        totals["blank"] += raw.blank
    ratio = round(100 * totals["comments"] / totals["sloc"], 2) if totals["sloc"] else 0.0
    return {**totals, "comment_ratio": ratio}
