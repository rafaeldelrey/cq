"""Lint findings for the UI, with its own eslint config."""

import json

from cq.context import Context
from cq.proc import require_node_modules, run
from cq.tally import ranked

KINDS = ("typescript",)


def collect(ctx: Context) -> dict:
    """eslint -f json; exit 1 means it found errors, still a measurement."""
    require_node_modules(ctx.app_dir)
    out = run(["npx", "eslint", "-f", "json", "."], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "eslint.json").write_text(out)
    files = json.loads(out)
    messages = [(f["filePath"], m, m.get("ruleId") or "parse") for f in files for m in f["messages"]]
    by_rule = ranked(rule for _, _, rule in messages)
    items = [
        {"file": ctx.rel(path), "line": m.get("line"), "code": rule, "msg": m["message"][:200]}
        for path, m, rule in messages[:30]
    ]
    return {
        "errors": sum(f["errorCount"] for f in files),
        "warnings": sum(f["warningCount"] for f in files),
        "files": len(files),
        "by_rule": by_rule,
        "items": items,
    }
