"""Type errors with the app's own pyright config (app and tests, as scripts/check.sh runs it)."""

import json

from cq.context import Context
from cq.proc import ToolError, run_app_tool

KINDS = ("python",)


def collect(ctx: Context) -> dict:
    """pyright --outputjson; exit 1 means "found errors", which is still a measurement."""
    out = run_app_tool(("pyright",), ["pyright", "--outputjson"], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "pyright.json").write_text(out)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ToolError(f"pyright printed no JSON: {out[-500:]}") from exc
    summary = data["summary"]
    diagnostics = data.get("generalDiagnostics", [])
    errors = [d for d in diagnostics if d.get("severity") == "error"]
    by_rule: dict[str, int] = {}
    for d in errors:
        rule = d.get("rule", "other")
        by_rule[rule] = by_rule.get(rule, 0) + 1
    return {
        "errors": summary["errorCount"],
        "warnings": summary["warningCount"],
        "files": summary["filesAnalyzed"],
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "items": [
            {"file": ctx.rel(d["file"]), "line": d["range"]["start"]["line"] + 1, "msg": d["message"][:200]}
            for d in errors[:30]
        ],
    }
