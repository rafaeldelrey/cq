"""Copy-pasted code (jscpd, via npx): the share of lines that are part of a clone."""

import json

from cq.context import Context
from cq.proc import run

KINDS = ("python", "typescript")

FORMATS = {"python": "python", "typescript": "typescript,tsx,javascript,jsx"}
IGNORE = "**/tests/**,**/*.test.ts,**/*.test.tsx,**/node_modules/**,**/.next/**"


def collect(ctx: Context) -> dict:
    """Clones of at least 50 tokens and 5 lines (jscpd's defaults) across the production paths."""
    out_dir = ctx.raw_dir / "jscpd"
    paths = [str(p) for p in ctx.sources]
    run(
        [
            "npx", "-y", ctx.settings.jscpd, *paths,
            "--format", FORMATS[ctx.target.kind],
            "--ignore", IGNORE,
            "--reporters", "json",
            "--output", str(out_dir),
            "--silent",
            "--gitignore",
        ],
        ctx.checkout,
    )  # fmt: skip
    data = json.loads((out_dir / "jscpd-report.json").read_text())
    total = data["statistics"]["total"]
    clones = sorted(data["duplicates"], key=lambda d: -d["lines"])
    return {
        "percent": total["percentage"],
        "clones": total["clones"],
        "duplicated_lines": total["duplicatedLines"],
        "lines": total["lines"],
        "top": [
            {
                "lines": c["lines"],
                "a": f"{ctx.rel(c['firstFile']['name'], ctx.checkout)}:{c['firstFile']['start']}",
                "b": f"{ctx.rel(c['secondFile']['name'], ctx.checkout)}:{c['secondFile']['start']}",
            }
            for c in clones[:15]
        ],
    }
