"""Broken links in the docs (lychee, offline): the paths in ``link_paths``.

A renamed or deleted file leaves links that point nowhere, and a reader (or an agent) following
the docs hits a dead end. Offline mode checks links to files and anchors in the repository only
(deterministic, no network); web links are not checked. ``link_exclude`` leaves out frozen
history whose old paths are meant to stay.
"""

import json
import shutil

from cq.context import Context
from cq.proc import ToolError, run
from cq.tally import ranked

KINDS = ("repo",)


def collect(ctx: Context) -> dict:
    """Run lychee over the configured paths; list every broken link with the file it is in."""
    if shutil.which("lychee") is None:
        raise ToolError("lychee is not installed (brew install lychee)")
    paths = [p for p in ctx.settings.link_paths if (ctx.checkout / p).exists()]
    excludes = [arg for p in ctx.settings.link_exclude for arg in ("--exclude-path", p)]
    out_file = ctx.raw_dir / "lychee.json"
    run(
        ["lychee", "--offline", "--no-progress", "--format", "json", "--output", str(out_file), *excludes, *paths],
        ctx.checkout,
        ok_codes=(0, 2),  # 2: broken links found
    )
    data = json.loads(out_file.read_text())
    root = f"file://{ctx.checkout.resolve()}/"
    items = [
        {
            "file": ctx.rel(source, ctx.checkout),
            "target": e["url"].replace(root, ""),
            "status": str(e["status"]["text"])[:80],
        }
        for source, errors in data["error_map"].items()
        for e in errors
    ]
    by_file = ranked(i["file"] for i in items)
    return {
        "checked": data.get("total", 0),
        "broken": len(items),
        "files_with_broken": len(by_file),
        "by_file": dict(list(by_file.items())[:20]),
        "items": items[:150],
    }
