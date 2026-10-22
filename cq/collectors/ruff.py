"""Lint findings and unformatted files, with the app's own ruff config (its pyproject)."""

import json
import re

from cq.context import Context
from cq.proc import ToolError, run_app_tool
from cq.tally import ranked

KINDS = ("python",)

# ruff format --check's summary, on stdout in every version: "2 files would be reformatted, 1 file
# already formatted". Up to 0.15 each file also had a "Would reformat: <path>" line; 0.16 replaced
# those with diagnostics, so the summary is what stays.
WOULD_REFORMAT = re.compile(r"^(\d+) files? would be reformatted", re.MULTILINE)


def unformatted_count(output: str) -> int:
    """How many files ``ruff format --check`` would reformat; never a silent 0.

    No summary line means nothing to reformat, unless the output still says a file would be: then
    the format is one cq can't read, and that is a tool error, not a clean result.
    """
    m = WOULD_REFORMAT.search(output)
    if m:
        return int(m.group(1))
    if "would be reformatted" in output.lower():
        raise ToolError(f"ruff format --check: files to reformat, but no count cq can read: {output[-300:]}")
    return 0


def collect(ctx: Context) -> dict:
    """ruff check over the whole app (its config decides the scope), and ruff format --check."""
    out = run_app_tool(("ruff",), ["ruff", "check", "--output-format", "json", "--exit-zero", "."], ctx.app_dir)
    (ctx.raw_dir / "ruff.json").write_text(out)
    findings = json.loads(out or "[]")
    by_rule = ranked(f.get("code") or "syntax" for f in findings)
    fmt = run_app_tool(("ruff",), ["ruff", "format", "--check", "."], ctx.app_dir, ok_codes=(0, 1))
    return {
        "violations": len(findings),
        "fixable": sum(1 for f in findings if f.get("fix")),
        "by_rule": by_rule,
        "unformatted_files": unformatted_count(fmt),
        "items": [
            {"file": ctx.rel(f["filename"]), "line": f["location"]["row"], "code": f.get("code"), "msg": f["message"]}
            for f in ctx.cap(findings, 30)
        ],
    }
