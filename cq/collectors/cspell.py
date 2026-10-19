"""cspell: unknown words in production code, against the repo's cspell word list (cq/toolconfig.py).

Two kinds of real finding come out of it: typos, and personal names hardcoded in production code.
A project's word list should leave names out so they keep showing.
Reported only: a growing word list makes the count noisy as a gate.

The words themselves are never stored, only where they are: an unknown word can be a person's name
or a fragment of a key, and run folders may be served to others. Open the file at the line to see it.
"""

import re

from cq.context import Context
from cq.proc import require, run
from cq.toolconfig import cspell_config

KINDS = ("python", "typescript")

LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):\d+ - Unknown word \((?P<word>[^)]+)\)")
GLOBS = {"python": "**/*.py", "typescript": "**/*.{ts,tsx}"}


def collect(ctx: Context) -> dict:
    """cspell lint over the production paths; one line per unknown word occurrence."""
    require("npx")
    globs = [
        f"{p.relative_to(ctx.checkout)}/{GLOBS[ctx.target.kind]}" if p.is_dir() else str(p.relative_to(ctx.checkout))
        for p in ctx.sources
    ]
    out = run(
        [
            "npx",
            "-y",
            ctx.settings.cspell,
            "lint",
            "--config",
            str(cspell_config(ctx.settings)),
            "--no-progress",
            "--no-summary",
            "--no-color",
            *globs,
        ],
        ctx.checkout,
        ok_codes=(0, 1),
        secret_output=True,  # the words can be names or key fragments: not in a log that may be served to others
    )
    hits = [m.groupdict() for m in map(LINE.match, out.splitlines()) if m]
    return {
        "unknown_words": len({h["word"] for h in hits}),
        "occurrences": len(hits),
        "items": ctx.with_fingerprints(
            [{"file": ctx.rel(h["file"], ctx.checkout), "line": int(h["line"])} for h in hits[:150]]
        ),
    }
