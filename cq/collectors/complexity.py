"""Cyclomatic complexity per function, measured exactly as scripts/complexity_gate.py measures it.

The gate's own ``measure_source`` is loaded from the repo (not the checkout being measured), so a
snapshot of an old commit is counted with today's rules and stays comparable.
"""

import importlib.util
import json
from statistics import mean

from radon.complexity import cc_rank

from cq.collectors.size import python_files
from cq.config import REPO_ROOT
from cq.context import Context

KINDS = ("python",)

_spec = importlib.util.spec_from_file_location("complexity_gate", REPO_ROOT / "scripts" / "complexity_gate.py")
assert _spec and _spec.loader  # noqa: S101 - the gate is part of this repo; a missing file is a broken checkout
complexity_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(complexity_gate)


def measure(ctx: Context) -> dict[str, int]:
    """``<file>::<function>`` → CC for every production function of the target."""
    result: dict[str, int] = {}
    for file in python_files(ctx.sources):
        rel = ctx.rel(file)
        for name, cc in complexity_gate.measure_source(file.read_text()).items():
            result[f"{rel}::{name}"] = cc
    return result


def baseline_entries(ctx: Context) -> int:
    """Grandfathered functions of this target in the checkout's .complexity-baseline.json."""
    path = ctx.checkout / ".complexity-baseline.json"
    if not path.exists():
        return 0
    functions = json.loads(path.read_text())["functions"]
    return sum(1 for key in functions if key.startswith(ctx.target.root_in(ctx.checkout) + "/"))


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
        "worst": [{"function": k, "cc": v, "rank": cc_rank(v)} for k, v in worst[:15]],
    }
