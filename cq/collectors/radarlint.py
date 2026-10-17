"""radarlint: SonarSource's analyzers (the SonarLint rules for Python and JS/TS), run through qlty.

Hundreds of bug and code-smell rules that ruff and eslint don't have, without a SonarQube server.
The trial (#284) found real ones: redundant exception classes, duplicated regex ranges,
commented-out code, nested ternaries. Rules another collector already measures are left out, and
test files are dropped (float equality and empty blocks there are noise).
"""

from cq import qlty
from cq.context import Context

KINDS = ("python", "typescript")

PLUGIN = {"python": "radarlint-python", "typescript": "radarlint-js"}
# Already measured elsewhere: cognitive complexity (complexipy), return types (pyright, tsc).
OVERLAPPING = {"python:S3776", "python:S5886", "typescript:S3776", "javascript:S3776"}


def collect(ctx: Context) -> dict:
    """radarlint over the target's production paths."""
    plugin = PLUGIN[ctx.target.kind]
    rows = qlty.run_sarif(ctx, ["check", "--no-fail", "--filter", plugin, *qlty.source_args(ctx)], "radarlint")
    kept = [r for r in rows if r["rule"] not in OVERLAPPING and not qlty.is_test(r["file"])]
    by_rule: dict[str, int] = {}
    for r in kept:
        by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
    return {
        "findings": len(kept),
        "warnings": sum(1 for r in kept if r["severity"] == "warning"),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "items": ctx.with_fingerprints(
            [
                {"file": r["file"], "line": r["line"], "code": r["rule"], "severity": r["severity"], "msg": r["msg"]}
                for r in kept[:120]
            ]
        ),
    }
