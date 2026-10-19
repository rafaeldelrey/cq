"""Maintainability index per file (radon mi): 100 is trivial, under 20 (rank B/C) is hard going."""

from statistics import mean

from radon.metrics import mi_rank, mi_visit

from cq.collectors.size import python_files
from cq.context import Context

KINDS = ("python",)


def collect(ctx: Context) -> dict:
    """Mean MI (plain and weighted by lines), rank counts, and the lowest-scoring files."""
    rows: list[tuple[str, float, int]] = []
    for file in python_files(ctx.sources):
        source = file.read_text()
        rows.append((ctx.rel(file), mi_visit(source, multi=True), source.count("\n") + 1))
    ranks = dict.fromkeys("ABC", 0)
    for _, mi, _ in rows:
        ranks[mi_rank(mi)] += 1
    total_lines = sum(lines for _, _, lines in rows)
    rows.sort(key=lambda r: r[1])
    return {
        "files": len(rows),
        "mean": round(mean(mi for _, mi, _ in rows), 2) if rows else 0.0,
        "weighted_mean": round(sum(mi * lines for _, mi, lines in rows) / total_lines, 2) if total_lines else 0.0,
        "ranks": ranks,
        "worst": [
            {"file": f, "mi": round(mi, 2), "rank": mi_rank(mi), "lines": n} for f, mi, n in ctx.cap(rows, 15, "worst")
        ],
    }
