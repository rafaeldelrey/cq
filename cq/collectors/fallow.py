"""fallow: the UI's near-duplicates and function health (complexity × coverage risk).

`fallow dupes` finds copy-paste including structurally similar blocks (jscpd: exact only).
`fallow health` scores every function by cyclomatic and cognitive complexity, size and an estimated
CRAP risk (complexity weighed against test coverage): the hotspot view the Python apps get from
the hotspots collector. Its dead-code view overlaps knip, so it isn't run.
"""

from cq.context import Context
from cq.proc import parse_json, require, require_node_modules, run

KINDS = ("typescript",)

SEVERITY = {"critical": "warning", "high": "warning", "moderate": "info", "low": "info"}


def run_fallow(ctx: Context, command: str) -> dict:
    """One fallow command in the app directory, JSON. fallow exits 1 when its own gate trips."""
    out = run(["npx", "-y", ctx.settings.fallow, command, "--format", "json"], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / f"fallow-{command}.json").write_text(out)
    data = parse_json(out, f"fallow {command}")
    return data if isinstance(data, dict) else {}


def collect(ctx: Context) -> dict:
    """Duplication and function health for the UI."""
    require("npx")
    require_node_modules(ctx.app_dir)
    dupes = run_fallow(ctx, "dupes")
    health = run_fallow(ctx, "health")
    stats = dupes.get("stats", {})
    summary = health.get("summary", {})
    findings = health.get("findings", [])
    return {
        "health_score": (health.get("health_score") or {}).get("score"),
        "duplication_percent": round(stats.get("duplication_percentage", 0.0), 2),
        "clone_groups": stats.get("clone_groups", 0),
        "functions_over_limits": summary.get("functions_above_threshold", 0),
        "critical": summary.get("severity_critical_count", 0),
        "high": summary.get("severity_high_count", 0),
        "clones": [
            {
                "fingerprint": g.get("fingerprint"),
                "lines": g.get("line_count"),
                "a": f"{ctx.rel(g['instances'][0]['file'])}:{g['instances'][0]['start_line']}",
                "b": f"{ctx.rel(g['instances'][1]['file'])}:{g['instances'][1]['start_line']}",
            }
            for g in ctx.cap(
                [g for g in dupes.get("clone_groups", []) if len(g.get("instances", [])) >= 2], 40, "clones"
            )
        ],
        "items": ctx.with_fingerprints(
            [
                {
                    "file": ctx.rel(f["path"]),
                    "line": f.get("line"),
                    "function": f.get("name", ""),
                    "severity": SEVERITY.get(f.get("severity", "moderate"), "info"),
                    "cyclomatic": f.get("cyclomatic"),
                    "cognitive": f.get("cognitive"),
                    "crap": f.get("crap"),
                }
                for f in ctx.cap(findings, 80)
            ]
        ),
    }
