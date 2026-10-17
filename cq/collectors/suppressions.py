"""Suppressions: the comments that silence a check instead of fixing what it found.

Each one can be right (a documented reason), but together they are debt the other tools can't
see: a rising count means problems are being hidden faster than fixed. Production code only.
"""

import re
from pathlib import Path

from cq.collectors.size import python_files
from cq.context import Context

KINDS = ("python", "typescript")

PATTERNS: dict[str, re.Pattern[str]] = {
    "noqa": re.compile(r"#\s*noqa\b"),
    "type-ignore": re.compile(r"#\s*type:\s*ignore\b"),
    "pyright-ignore": re.compile(r"#\s*pyright:\s*ignore\b"),
    "nosemgrep": re.compile(r"(#|//)\s*nosemgrep\b"),
    "no-cover": re.compile(r"#\s*pragma:\s*no\s*cover\b"),
    "fmt-skip": re.compile(r"#\s*fmt:\s*(skip|off)\b"),
    "gitleaks-allow": re.compile(r"gitleaks:allow\b"),
    "eslint-disable": re.compile(r"eslint-disable"),
    "ts-ignore": re.compile(r"@ts-(ignore|nocheck)\b"),
    "ts-expect-error": re.compile(r"@ts-expect-error\b"),
}
REASON = re.compile(r"(\s-\s|—|:\s+\w+.*\w{3})")  # a reason after " - ", an em dash, or a "code: words" comment
TS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mts"}


def source_files(ctx: Context) -> list[Path]:
    """Production files of the target, tests excluded."""
    if ctx.target.kind == "python":
        return python_files(ctx.sources)
    files: list[Path] = []
    for path in ctx.sources:
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        files.extend(
            f for f in candidates
            if f.suffix in TS_SUFFIXES and ".test." not in f.name and "node_modules" not in f.parts
        )  # fmt: skip
    return files


def collect(ctx: Context) -> dict:
    """Count suppressions by kind; list them with whether the comment gives a reason."""
    items = []
    for file in source_files(ctx):
        for number, line in enumerate(file.read_text(errors="replace").splitlines(), 1):
            for kind, pattern in PATTERNS.items():
                m = pattern.search(line)
                if m:
                    comment = line[m.start() :].strip()
                    items.append({
                        "file": ctx.rel(file),
                        "line": number,
                        "kind": kind,
                        "reason": bool(REASON.search(comment[len(m.group(0)):])),
                        "text": comment[:160],
                    })  # fmt: skip
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    return {
        "total": len(items),
        "without_reason": sum(1 for i in items if not i["reason"]),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "items": sorted(items, key=lambda i: (i["reason"], i["file"], i["line"]))[:150],
    }
