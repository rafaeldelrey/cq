"""Lint findings for the UI, with its own eslint config."""

import json

from cq.context import Context
from cq.proc import require_node_modules, run
from cq.tally import ranked

KINDS = ("typescript",)


def rule_of(message: dict) -> str:
    """An eslint message's rule; a parse error has none."""
    return message.get("ruleId") or "parse"


def collect(ctx: Context) -> dict:
    """eslint -f json; exit 1 means it found errors, still a measurement."""
    require_node_modules(ctx.app_dir)
    out = run(["npx", "eslint", "-f", "json", "."], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "eslint.json").write_text(out)
    files = json.loads(out)
    messages = [(f["filePath"], m) for f in files for m in f["messages"]]
    by_rule = ranked(rule_of(m) for _, m in messages)
    items = [
        {"file": ctx.rel(path), "line": m.get("line"), "code": rule_of(m), "msg": m["message"][:200]}
        for path, m in ctx.cap(messages, 30)
    ]
    return {
        "errors": sum(f["errorCount"] for f in files),
        "warnings": sum(f["warningCount"] for f in files),
        "files": len(files),
        "by_rule": by_rule,
        "items": items,
    }
