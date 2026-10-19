"""Type errors for the UI: its `typecheck` script (next typegen, then tsc --noEmit)."""

import re

from cq.context import Context
from cq.proc import require_node_modules, run
from cq.tally import ranked

KINDS = ("typescript",)

ERROR = re.compile(r"^(?P<file>[^\s(]+)\((?P<line>\d+),\d+\): error (?P<code>TS\d+): (?P<msg>.*)$")


def collect(ctx: Context) -> dict:
    """Count tsc's ``error TS<code>`` lines; exit 2 (tsc) or 1 (npm) means it found some."""
    require_node_modules(ctx.app_dir)
    out = run(["npm", "run", "--silent", "typecheck", "--", "--pretty", "false"], ctx.app_dir, ok_codes=(0, 1, 2))
    (ctx.raw_dir / "tsc.txt").write_text(out)
    errors = [m.groupdict() for m in map(ERROR.match, out.splitlines()) if m]
    by_code = ranked(e["code"] for e in errors)
    return {
        "errors": len(errors),
        "by_rule": by_code,
        "items": [
            {"file": e["file"], "line": int(e["line"]), "code": e["code"], "msg": e["msg"][:200]} for e in errors[:30]
        ],
    }
