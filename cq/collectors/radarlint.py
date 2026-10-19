"""radarlint: SonarSource's analyzers (the SonarLint rules for Python and JS/TS), run through qlty.

Hundreds of bug and code-smell rules that ruff and eslint don't have, without a SonarQube server.
Real ones it finds: redundant exception classes, duplicated regex ranges,
commented-out code, nested ternaries. Rules another collector already measures are left out, and
test files are dropped (float equality and empty blocks there are noise).
"""

from cq import qlty
from cq.context import Context
from cq.tally import ranked

KINDS = ("python", "typescript")

PLUGIN = {"python": "radarlint-python", "typescript": "radarlint-js"}
# Already measured elsewhere: cognitive complexity (complexipy), return types (pyright, tsc).
OVERLAPPING = {"python:S3776", "python:S5886", "typescript:S3776", "javascript:S3776"}


def collect(ctx: Context) -> dict:
    """radarlint over the target's production paths."""
    plugin = PLUGIN[ctx.target.kind]
    rows = qlty.run_sarif(ctx, ["check", "--no-fail", "--filter", plugin, *qlty.source_args(ctx)], "radarlint")
    kept = [r for r in qlty.production(rows) if r["rule"] not in OVERLAPPING]
    return {
        "findings": len(kept),
        "warnings": sum(1 for r in kept if r["severity"] == "warning"),
        "by_rule": ranked(r["rule"] for r in kept),
        "items": qlty.items(ctx, kept, "severity"),
    }
