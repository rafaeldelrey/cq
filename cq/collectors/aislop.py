"""aislop: deterministic rules for what AI coding agents leave behind (no LLM at run time).

Its `ai-slop` engine finds swallowed errors that drop the cause, comments that narrate decisions
or plans, TODO stubs, double type assertions, isinstance ladders; its other engines add complexity,
duplicate blocks and a few security checks. aislop reports nothing in test files but counts them in
its score, so they are excluded.

``narrative-comment`` flags decorative section separators and ``hardcoded-url`` config defaults,
so both are dropped. aislop's own 0–100 score is kept as
reported (it still counts them); the counts here don't.

aislop's Python dependency audit runs whatever bare ``pip-audit`` is on PATH, which audits the
environment it is installed in, never the measured repo. So aislop runs without the bin folder of
cq's own environment on its PATH (under ``uv run cq`` that folder holds cq's pip-audit, and the
findings would be cq's own dependencies), and its "Vulnerable Python dependency" findings are
dropped: cq's ``vulnerabilities`` collector audits each app's lock. Its npm audit findings stay,
as they audit the app's own package.json.
"""

import os
import sysconfig
from pathlib import Path

from cq.context import Context
from cq.proc import parse_json, require, run
from cq.tally import ranked

KINDS = ("python", "typescript")

DROPPED = {"ai-slop/narrative-comment", "ai-slop/hardcoded-url"}
EXCLUDE = "tests/**,**/tests/**,**/test_*.py,**/*_test.py,**/*.test.ts,**/*.test.tsx,**/*.spec.ts,**/*.spec.tsx"
SEVERITY = {"error": "error", "warning": "warning", "info": "info"}
PYTHON_AUDIT = "Vulnerable Python dependency"


def dropped(diagnostic: dict) -> bool:
    """A rule cq leaves out, or aislop's audit of an environment that isn't the repo's."""
    rule = diagnostic.get("rule")
    if rule in DROPPED:
        return True
    return rule == "security/vulnerable-dependency" and diagnostic.get("message", "").startswith(PYTHON_AUDIT)


def path_without_cq() -> str:
    """PATH without the scripts folder of the Python environment cq runs in."""
    own = Path(sysconfig.get_path("scripts")).resolve()
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(e for e in entries if e and Path(e).resolve() != own)


def collect(ctx: Context) -> dict:
    """aislop scan over the app directory, JSON."""
    require("npx")
    out = run(
        ["npx", "-y", ctx.settings.aislop, "scan", str(ctx.app_dir), "--json", "--exclude", EXCLUDE],
        ctx.checkout,
        ok_codes=(0, 1),
        env={"PATH": path_without_cq()},
    )
    (ctx.raw_dir / "aislop.json").write_text(out)
    data = parse_json(out, "aislop")
    kept = [d for d in data.get("diagnostics", []) if not dropped(d)]
    by_rule = ranked(d["rule"] for d in kept)
    return {
        "score": data.get("score"),
        "findings": len(kept),
        "slop": sum(1 for d in kept if d.get("engine") == "ai-slop"),
        "by_rule": by_rule,
        "items": ctx.with_fingerprints(
            [
                {
                    "file": ctx.rel(d["filePath"]),
                    "line": d.get("line"),
                    "code": d["rule"],
                    "engine": d.get("engine", ""),
                    "severity": SEVERITY.get(d.get("severity", "warning"), "warning"),
                    "msg": d.get("message", "")[:240],
                }
                for d in ctx.cap(kept, 120)
            ]
        ),
    }
