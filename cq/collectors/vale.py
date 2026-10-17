"""vale with the AISlop style: AI-written prose and stub language in docs, comments and docstrings.

The style (cq's AISlop in cq/defaults/vale/styles, unless the repo has its own .vale.ini) flags the words and
phrases that give AI prose away as warnings, and stub language (text standing in for code that was never
written) as errors; the word lists are in the style's rule files. It runs on the docs lychee checks (`link_paths`)
and on the production code of every app, where vale reads only comments and docstrings.
"""

import os
from pathlib import Path

from cq.context import Context
from cq.proc import ToolError, parse_json, require, run
from cq.toolconfig import vale_config

KINDS = ("repo",)

SEVERITY = {"error": "error", "warning": "warning", "suggestion": "info"}


EXTENSIONS = (".md", ".py", ".ts", ".tsx")


def skipped(rel: str, skip: tuple[str, ...]) -> bool:
    """Whether a repo-relative path is, or is inside, an excluded path or folder name."""
    parts = rel.split("/")
    return any(rel == s or rel.startswith(f"{s}/") or ("/" not in s and s in parts) for s in skip)


def files(ctx: Context, roots: list[str]) -> list[str]:
    """The files vale reads under the roots, with excluded folders pruned before vale ever sees them."""
    repo = ctx.settings.repo_root
    produced = (ctx.settings.history_dir, ctx.settings.runs_dir)  # cq's own output, not the repo's prose
    own = [p.relative_to(repo).as_posix() for p in produced if p.is_relative_to(repo)]
    skip = tuple(s.rstrip("/") for s in (*ctx.settings.link_exclude, *own))
    found = []
    for root in roots:
        if (ctx.checkout / root).is_file():
            found += [root] if root.endswith(EXTENSIONS) and not skipped(root, skip) else []
            continue
        for d, dirs, names in os.walk(ctx.checkout / root):
            rel = Path(d).relative_to(ctx.checkout).as_posix()
            dirs[:] = sorted(x for x in dirs if not skipped(f"{rel}/{x}", skip))
            found += [f"{rel}/{n}" for n in sorted(names) if n.endswith(EXTENSIONS) and not skipped(f"{rel}/{n}", skip)]
    return found


def paths(ctx: Context) -> list[str]:
    """Docs plus every app's production paths, repo-relative, minus the excluded ones."""
    docs = [p for p in ctx.settings.link_paths if (ctx.checkout / p).exists()]
    code = [
        str(p.relative_to(ctx.checkout))
        for t in ctx.settings.targets
        if t.kind in ("python", "typescript")
        for p in t.source_paths(ctx.checkout)
    ]
    return files(ctx, [*docs, *code])


def collect(ctx: Context) -> dict:
    """vale over docs and production code, JSON."""
    require("vale")
    targets = paths(ctx)
    if not targets:  # vale with no paths would read stdin
        raise ToolError("vale: no files to check (link_paths and the apps' sources are all excluded or missing)")
    out = run(
        ["vale", "--config", str(vale_config(ctx.settings)), "--output", "JSON", *targets],
        ctx.checkout,
        ok_codes=(0, 1),
    )
    (ctx.raw_dir / "vale.json").write_text(out)
    data = parse_json(out or "{}", "vale")
    if isinstance(data, dict) and "Code" in data:  # vale's own error object (a broken style)
        raise ToolError(f"vale: {data.get('Text', data)}")
    items = [
        {
            "file": ctx.rel(path, ctx.checkout),
            "line": a.get("Line"),
            "code": a.get("Check", "").split(".")[-1],
            "severity": SEVERITY.get(a.get("Severity", "warning"), "warning"),
            "match": a.get("Match", ""),
            "msg": a.get("Message", "")[:200],
        }
        for path, alerts in data.items()
        for a in alerts
    ]
    by_match: dict[str, int] = {}
    for item in items:
        word = item["match"].lower()
        by_match[word] = by_match.get(word, 0) + 1
    return {
        "findings": len(items),
        "stubs": sum(1 for i in items if i["code"] == "Stubs"),
        "prose": sum(1 for i in items if i["code"] == "Words"),
        "by_match": dict(sorted(by_match.items(), key=lambda kv: -kv[1])),
        "items": ctx.with_fingerprints(ctx.cap(items, 120)),
    }
