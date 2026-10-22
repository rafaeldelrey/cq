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
    # The summary is the last line; diagnostics (an unparsable file's, from ruff 0.16) may come first.
    if not output.strip() or ALL_FORMATTED.fullmatch(output.strip().splitlines()[-1]):
        return 0
    raise ToolError(f"ruff format --check: output with no count cq can read: {output[-300:]}")


EXIT_UNPARSABLE = 2  # ruff format's exit code when a file can't be parsed: it still checked, and summed up, the rest


def format_check(ctx: Context, extra: list[str]) -> str:
    """``ruff format --check``'s stdout, also when a file can't be parsed.

    That file's syntax error is ``ruff check``'s finding; the summary still counts the other files.
    Exit 2 without a summary (a config error, a crash) stays a tool error, with ruff's message.
    """
    try:
        return run_app_tool(("ruff",), ["ruff", "format", "--check", *extra, "."], ctx.app_dir, ok_codes=(0, 1))
    # A specific, expected exception: exit 2 after a summary is a measurement; anything else propagates.
    except ToolError as exc:
        if exc.returncode == EXIT_UNPARSABLE and (
            WOULD_REFORMAT.search(exc.stdout) or ALL_FORMATTED.search(exc.stdout)
        ):
            return exc.stdout
        raise


GLOB_SPECIAL = set("[]*?{}")


def glob_literal(path: str) -> str:
    """``path`` as a ruff exclude pattern that matches only itself: ruff reads excludes as globs, so
    each glob character goes in a one-character class (``[[]``, ``[*]``).

    A comma or a backslash can't be passed: ``--extend-exclude`` splits its value on commas, ruff's
    globs read a backslash as an escape, and overriding the setting through ``--config`` would
    replace the app's own. That's a tool error, never a miscount.
    """
    for char, name in ((",", "a comma"), ("\\", "a backslash")):
        if char in path:
            raise ToolError(f"cq can't exclude the nested target {path!r} from ruff: its path has {name}")
    return "".join(f"[{c}]" if c in GLOB_SPECIAL else c for c in path)


OLD_FORMAT = "unexpected argument '--extend-exclude'"  # ruff before 0.16: `format` has no such flag


def format_count(ctx: Context, nested: list[str]) -> int:
    """Files ``ruff format --check`` would reformat, of this target's own.

    ruff 0.16 and later exclude nested targets with ``--extend-exclude``. Older ruff rejects that
    flag on ``format``, but lists each file on a "Would reformat: <path>" line, so cq counts the
    lines whose file the target owns.
    """
    try:
        return unformatted_count(format_check(ctx, nested))
    # A specific, expected exception: an older ruff refusing the flag; any other failure propagates.
    except ToolError as exc:
        if not nested or OLD_FORMAT not in str(exc):
            raise
    out = format_check(ctx, [])
    roots = ctx.nested_roots
    files = [line.removeprefix("Would reformat: ") for line in out.splitlines() if line.startswith("Would reformat: ")]
    return sum(1 for f in files if ctx.owns(f, roots))


def collect(ctx: Context) -> dict:
    """ruff check over the whole app (its config decides the scope), and ruff format --check."""
    # A target nested inside this one counts its own files (#48). "./" anchors each pattern to the
    # app directory: a bare name would exclude every folder of that name in the app, and hide it.
    nested = [
        arg
        for root in ctx.nested_roots
        for arg in ("--extend-exclude", f"./{glob_literal(root.relative_to(ctx.app_dir.resolve()).as_posix())}")
    ]
    out = run_app_tool(
        ("ruff",), ["ruff", "check", "--output-format", "json", "--exit-zero", *nested, "."], ctx.app_dir
    )
    (ctx.raw_dir / "ruff.json").write_text(out)
    findings = json.loads(out or "[]")
    by_rule = ranked(f.get("code") or "syntax" for f in findings)
    return {
        "violations": len(findings),
        "fixable": sum(1 for f in findings if f.get("fix")),
        "by_rule": by_rule,
        "unformatted_files": format_count(ctx, nested),
        "items": [
            {"file": ctx.rel(f["filename"]), "line": f["location"]["row"], "code": f.get("code"), "msg": f["message"]}
            for f in ctx.cap(findings, 30)
        ],
    }
