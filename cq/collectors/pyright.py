"""Type errors with the app's own pyright config (app and tests, as the app's own checks run it).

Only the target's own files count (``Context.owns``): an app without a pyright config of its own
picks up an ancestor's, which reaches into other apps (#47), and a target nested inside this one
counts its own files (#48). ``files`` is still pyright's ``filesAnalyzed``: pyright doesn't list
the files it analysed.
"""

import json

from cq.context import Context
from cq.proc import ToolError, run_app_tool
from cq.tally import ranked

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
    nested = ctx.nested_roots
    diagnostics = [d for d in data.get("generalDiagnostics", []) if ctx.owns(d.get("file", ""), nested)]
    errors = [d for d in diagnostics if d.get("severity") == "error"]
    by_rule = ranked(d.get("rule", "other") for d in errors)
    return {
        "errors": len(errors),
        "warnings": sum(1 for d in diagnostics if d.get("severity") == "warning"),
        "files": summary["filesAnalyzed"],
        "by_rule": by_rule,
        "items": [
            {"file": ctx.rel(d["file"]), "line": d["range"]["start"]["line"] + 1, "msg": d["message"][:200]}
            for d in ctx.cap(errors, 30)
        ],
    }
