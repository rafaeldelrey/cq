"""pyscn: Python structure, in particular renamed copy-paste (type-2 and type-3 clones).

jscpd only matches identical token runs; pyscn also finds a block copied and renamed, the way an
AI agent re-creates a helper instead of reusing it. It also reports import cycles and classes with
high coupling or low cohesion. Its dead-code and complexity overlap vulture and radon, so only the
structure measures are kept here.
"""

from cq.context import Context
from cq.proc import parse_json, require, run

KINDS = ("python",)

MIN_LINES = 6  # shorter clones are mostly shared idioms (a guard clause, a log call)


def collect(ctx: Context) -> dict:
    """pyscn analyze over the production paths, JSON on stdout."""
    require("uvx")
    paths = [str(p) for p in ctx.sources]
    out = run(
        ["uvx", ctx.settings.pyscn.replace("==", "@"), "analyze", "--json", "--output", "-", *paths], ctx.checkout
    )
    (ctx.raw_dir / "pyscn.json").write_text(out)
    data = parse_json(out, "pyscn")
    summary = data["summary"]
    pairs = [
        p for p in (data.get("clone") or {}).get("clone_pairs") or []  # null when pyscn finds no clones
        if (p["clone1"].get("line_count") or 0) >= MIN_LINES and "/tests/" not in p["clone1"]["location"]["file_path"]
    ]  # fmt: skip
    pairs.sort(key=lambda p: (-(p["clone1"].get("line_count") or 0), -p.get("similarity", 0)))
    return {
        "health_score": summary.get("health_score"),
        "duplication_percent": round(summary.get("code_duplication_percentage", 0.0), 2),
        "clone_pairs": len(pairs),
        "modules_in_cycles": summary.get("deps_modules_in_cycles", 0),
        "high_coupling_classes": summary.get("high_coupling_classes", 0),
        "low_cohesion_classes": summary.get("high_lcom_classes", 0),
        "items": [
            {
                "a": f"{ctx.rel(p['clone1']['location']['file_path'], ctx.checkout)}:{p['clone1']['location']['start_line']}",
                "b": f"{ctx.rel(p['clone2']['location']['file_path'], ctx.checkout)}:{p['clone2']['location']['start_line']}",
                "lines": p["clone1"].get("line_count"),
                "similarity": round(p.get("similarity", 0), 2),
                "type": p.get("type"),
                "at": "~".join(
                    ctx.fingerprint(ctx.rel(c["location"]["file_path"], ctx.checkout), c["location"]["start_line"])
                    for c in (p["clone1"], p["clone2"])
                ),
            }
            for p in ctx.cap(pairs, 60)
        ],
    }
