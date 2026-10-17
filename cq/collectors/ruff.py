"""Lint findings and unformatted files, with the app's own ruff config (its pyproject)."""

import json

from cq.context import Context
from cq.proc import run_app_tool

KINDS = ("python",)


def collect(ctx: Context) -> dict:
    """ruff check over the whole app (its config decides the scope), and ruff format --check."""
    out = run_app_tool(("ruff",), ["ruff", "check", "--output-format", "json", "--exit-zero", "."], ctx.app_dir)
    (ctx.raw_dir / "ruff.json").write_text(out)
    findings = json.loads(out or "[]")
    by_rule: dict[str, int] = {}
    for f in findings:
        code = f.get("code") or "syntax"
        by_rule[code] = by_rule.get(code, 0) + 1
    fmt = run_app_tool(("ruff",), ["ruff", "format", "--check", "."], ctx.app_dir, ok_codes=(0, 1))
    unformatted = [line.removeprefix("Would reformat: ") for line in fmt.splitlines() if line.startswith("Would")]
    return {
        "violations": len(findings),
        "fixable": sum(1 for f in findings if f.get("fix")),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "unformatted_files": len(unformatted),
        "items": [
            {"file": ctx.rel(f["filename"]), "line": f["location"]["row"], "code": f.get("code"), "msg": f["message"]}
            for f in findings[:30]
        ],
    }
