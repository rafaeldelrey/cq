"""Unused code (vulture), with the app's [tool.vulture], or cq's default ignores.

vulture guesses: 100% confidence (unused import, variable, unreachable code) is almost always
real; 60% (an unused function or class attribute) is often a framework reading it by name.
``high`` (≥ 90%) is the number to drive to zero; ``total`` is a trend, not a to-do list.
"""

import re

from cq.context import Context
from cq.proc import own, run
from cq.toolconfig import vulture_config, vulture_whitelist

KINDS = ("python",)

LINE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): (?P<msg>unused (?P<type>\w+)\b.*?|unreachable code.*?) \((?P<conf>\d+)% confidence"
)
HIGH_CONFIDENCE = 90


def parse(output: str, ctx: Context) -> list[dict]:
    """vulture's text lines as dicts."""
    items = []
    for line in output.splitlines():
        m = LINE.match(line)
        if m:
            items.append(
                {
                    "file": ctx.rel(m["file"], ctx.checkout),
                    "line": int(m["line"]),
                    "type": m["type"] or "unreachable",
                    "msg": m["msg"],
                    "confidence": int(m["conf"]),
                }
            )
    return items


def collect(ctx: Context) -> dict:
    """vulture exits 3 when it finds dead code; that is a measurement, not a failure."""
    paths = [str(p) for p in ctx.sources]
    whitelist = vulture_whitelist(ctx.settings, ctx.target)
    paths += [str(whitelist)] if whitelist else []
    config = vulture_config(ctx.settings, ctx.target)
    out = run([own("vulture"), *paths, "--config", str(config), "--exclude", "tests"], ctx.checkout, ok_codes=(0, 3))
    (ctx.raw_dir / "vulture.txt").write_text(out)
    items = parse(out, ctx)
    by_type: dict[str, int] = {}
    for item in items:
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
    high = [i for i in items if i["confidence"] >= HIGH_CONFIDENCE]
    return {
        "total": len(items),
        "high": len(high),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "items": sorted(items, key=lambda i: -i["confidence"])[:40],
    }
