"""Lint findings for the UI, with its own eslint config."""

import json

from cq.context import Context
from cq.proc import ToolError, run

KINDS = ("typescript",)


def collect(ctx: Context) -> dict:
    """eslint -f json; exit 1 means it found errors, still a measurement."""
    if not (ctx.app_dir / "node_modules").exists():
        raise ToolError("node_modules missing; run `npm ci` in the app first")
    out = run(["npx", "eslint", "-f", "json", "."], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "eslint.json").write_text(out)
    files = json.loads(out)
    by_rule: dict[str, int] = {}
    items = []
    for f in files:
        for m in f["messages"]:
            rule = m.get("ruleId") or "parse"
            by_rule[rule] = by_rule.get(rule, 0) + 1
            if len(items) < 30:
                items.append(
                    {"file": ctx.rel(f["filePath"]), "line": m.get("line"), "code": rule, "msg": m["message"][:200]}
                )
    return {
        "errors": sum(f["errorCount"] for f in files),
        "warnings": sum(f["warningCount"] for f in files),
        "files": len(files),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "items": items,
    }
