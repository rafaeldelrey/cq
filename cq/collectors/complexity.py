"""Cyclomatic complexity per function, measured exactly as ``cq complexity check`` measures it.

The gate's ``measure_source`` is cq's own, not the checkout's, so a snapshot of an old commit is
counted with today's rules and stays comparable.
"""

import json
from statistics import mean

from radon.complexity import cc_rank

from cq import complexity_gate
from cq.collectors.size import python_files
from cq.context import Context

KINDS = ("python",)


def measure(ctx: Context) -> dict[str, int]:
    """``<file>::<function>`` → CC for every production function of the target."""
    result: dict[str, int] = {}
    for file in python_files(ctx.sources):
        rel = ctx.rel(file)
        for name, cc in complexity_gate.measure_source(file.read_text()).items():
            result[f"{rel}::{name}"] = cc
    return result


def baseline_entries(ctx: Context) -> int:
    """Grandfathered functions of this target in the checkout's complexity baseline."""
    path = ctx.checkout / ctx.settings.complexity_baseline
    if not path.exists():
        return 0
    functions = json.loads(path.read_text())["functions"]
    root = ctx.target.root_in(ctx.checkout)
    return sum(1 for key in functions if root in (".", "") or key.startswith(f"{root}/"))


def collect(ctx: Context) -> dict:
    """Mean, rank distribution, count over the gate's limit, and the worst functions."""
    measured = measure(ctx)
    limit = ctx.settings.complexity_limit
    ranks = dict.fromkeys("ABCDEF", 0)
    for cc in measured.values():
        ranks[cc_rank(cc)] += 1
    worst = sorted(measured.items(), key=lambda kv: -kv[1])
    return {
        "functions": len(measured),
        "mean": round(mean(measured.values()), 2) if measured else 0.0,
        "max": worst[0][1] if worst else 0,
        "over_limit": sum(1 for cc in measured.values() if cc > limit),
        "limit": limit,
        "ranks": ranks,
        "baseline_entries": baseline_entries(ctx),
        # Every function over the limit and its CC: each is a finding, beyond the 15 worst listed.
        "over_limit_items": {k: v for k, v in worst if v > limit},
        "worst": [{"function": k, "cc": v, "rank": cc_rank(v)} for k, v in ctx.cap(worst, 15, "worst")],
    }
