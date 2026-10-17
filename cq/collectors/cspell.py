"""cspell: unknown words in production code, against code_quality/cspell.json's project word list.

Two kinds of real finding come out of it (trial, #284): typos (`hyperglicemia` in a danger-path
explanation) and personal or family names hardcoded in production code, which breaks the rule that
code stays family-agnostic. The word list deliberately leaves names out so they keep showing.
Reported only: a growing word list makes the count noisy as a gate.

The words themselves are never stored, only where they are: an unknown word can be a family name
or a fragment of a key, and run folders are served on the LAN. Open the file at the line to see it.
"""

import re

from cq.config import PROJECT_DIR
from cq.context import Context
from cq.proc import require, run

KINDS = ("python", "typescript")

CONFIG = PROJECT_DIR / "cspell.json"
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
        ["npx", "-y", ctx.settings.cspell, "lint", "--config", str(CONFIG), "--no-progress", "--no-summary",
         "--no-color", *globs],
        ctx.checkout,
        ok_codes=(0, 1),
        secret_output=True,  # the words can be names or key fragments: not in the LAN-served log
    )  # fmt: skip
    hits = [m.groupdict() for m in map(LINE.match, out.splitlines()) if m]
    return {
        "unknown_words": len({h["word"] for h in hits}),
        "occurrences": len(hits),
        "items": ctx.with_fingerprints(
            [{"file": ctx.rel(h["file"], ctx.checkout), "line": int(h["line"])} for h in hits[:150]]
        ),
    }
