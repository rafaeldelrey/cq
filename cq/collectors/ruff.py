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
ALL_FORMATTED = re.compile(r"\d+ files? already formatted")


def unformatted_count(output: str) -> int:
    """How many files ``ruff format --check`` would reformat; never a silent 0.

    The count is the summary's, the last one printed. Without one, the output must be empty or say
    only that every file is formatted; anything else is a format cq can't read, and a tool error.
    """
    counts = WOULD_REFORMAT.findall(output)
    if counts:
        return int(counts[-1])
    if not output.strip() or ALL_FORMATTED.fullmatch(output.strip()):
        return 0
    raise ToolError(f"ruff format --check: output with no count cq can read: {output[-300:]}")


def collect(ctx: Context) -> dict:
    """ruff check over the whole app (its config decides the scope), and ruff format --check."""
    # A target nested inside this one counts its own files (#48). "./" anchors each pattern to the
    # app directory: a bare name would exclude every folder of that name in the app, and hide it.
    nested = [
        arg
        for root in ctx.nested_roots
        for arg in ("--extend-exclude", f"./{root.relative_to(ctx.app_dir.resolve()).as_posix()}")
    ]
    out = run_app_tool(
        ("ruff",), ["ruff", "check", "--output-format", "json", "--exit-zero", *nested, "."], ctx.app_dir
    )
    (ctx.raw_dir / "ruff.json").write_text(out)
    findings = json.loads(out or "[]")
    by_rule = ranked(f.get("code") or "syntax" for f in findings)
    fmt = run_app_tool(("ruff",), ["ruff", "format", "--check", *nested, "."], ctx.app_dir, ok_codes=(0, 1))
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
