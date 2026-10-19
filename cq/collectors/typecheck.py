"""Type errors for the UI: its `typecheck` script (next typegen, then tsc --noEmit)."""

import re

from cq.context import Context
from cq.proc import ToolError, run

KINDS = ("typescript",)

ERROR = re.compile(r"^(?P<file>[^\s(]+)\((?P<line>\d+),\d+\): error (?P<code>TS\d+): (?P<msg>.*)$")


def collect(ctx: Context) -> dict:
    """Count tsc's ``error TS<code>`` lines; exit 2 (tsc) or 1 (npm) means it found some."""
    if not (ctx.app_dir / "node_modules").exists():
        raise ToolError("node_modules missing; run `npm ci` in the app first")
    out = run(["npm", "run", "--silent", "typecheck", "--", "--pretty", "false"], ctx.app_dir, ok_codes=(0, 1, 2))
    (ctx.raw_dir / "tsc.txt").write_text(out)
    errors = [m.groupdict() for m in map(ERROR.match, out.splitlines()) if m]
    by_code: dict[str, int] = {}
    for e in errors:
        by_code[e["code"]] = by_code.get(e["code"], 0) + 1
    return {
        "errors": len(errors),
        "by_rule": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "items": [
            {"file": e["file"], "line": int(e["line"]), "code": e["code"], "msg": e["msg"][:200]} for e in errors[:30]
        ],
    }
