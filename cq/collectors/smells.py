"""Structure smells and near-duplicates (`qlty smells`): the shapes AI-written code drifts into.

Too many parameters or return statements, deep nesting, complex boolean expressions, high file
complexity, and similar or identical code blocks (renamed copies included, which jscpd misses).
Thresholds are qlty's defaults except parameters (8, in `.qlty/qlty.toml`). Test files excluded.
"""

from cq import qlty
from cq.context import Context

KINDS = ("python", "typescript")

DUPLICATION = {"similar-code", "identical-code"}


def collect(ctx: Context) -> dict:
    """qlty smells over the target's production paths."""
    rows = qlty.run_sarif(ctx, ["smells", *qlty.source_args(ctx)], "smells")
    kept = [r for r in rows if not qlty.is_test(r["file"])]
    by_rule: dict[str, int] = {}
    for r in kept:
        by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
    return {
        "smells": len(kept),
        "structure": sum(1 for r in kept if r["rule"] not in DUPLICATION),
        "duplication": sum(1 for r in kept if r["rule"] in DUPLICATION),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "items": ctx.with_fingerprints(
            [
                {"file": r["file"], "line": r["line"], "code": r["rule"], "msg": r["msg"]}
                for r in sorted(kept, key=lambda r: -(r["properties"].get("actual") or 0))[:120]
            ]
        ),
    }
