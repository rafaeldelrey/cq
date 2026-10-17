"""Known vulnerabilities in dependencies: pip-audit on each Python app's lock, npm audit on the UI.

Both need network (the OSV / PyPI and npm advisory databases). The UI counts production
dependencies only (``--omit=dev``): what ships in the image.
"""

import json

from cq.context import Context
from cq.proc import ToolError, own, run

KINDS = ("python", "typescript")

NPM_LEVELS = ("critical", "high", "moderate", "low", "info")


def collect_python(ctx: Context) -> dict:
    """Export the app's locked requirements, then audit them without re-resolving."""
    if not (ctx.app_dir / "uv.lock").exists():
        raise ToolError("no uv.lock to audit")
    req = ctx.raw_dir / "requirements.txt"
    req.write_text(
        run(
            ["uv", "export", "-q", "--frozen", "--no-hashes", "--no-emit-project", "--format", "requirements-txt"],
            ctx.app_dir,
        )
    )
    out = ctx.raw_dir / "pip-audit.json"
    run(
        [own("pip-audit"), "-r", str(req), "--no-deps", "--disable-pip", "-f", "json", "-o", str(out)],
        ctx.checkout,
        ok_codes=(0, 1),
    )
    deps = json.loads(out.read_text())["dependencies"]
    vulnerable = [d for d in deps if d.get("vulns")]
    return {
        "packages": len(deps),
        "vulnerable_packages": len(vulnerable),
        "vulnerabilities": sum(len(d["vulns"]) for d in vulnerable),
        "items": [
            {
                "package": d["name"],
                "version": d["version"],
                "ids": ", ".join(v["id"] for v in d["vulns"]),
                "fix": ", ".join(sorted({fv for v in d["vulns"] for fv in v.get("fix_versions", [])})) or "none yet",
            }
            for d in vulnerable
        ],
    }


def collect_typescript(ctx: Context) -> dict:
    """npm audit of the production dependency tree."""
    out = run(["npm", "audit", "--json", "--omit=dev"], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "npm-audit.json").write_text(out)
    data = json.loads(out)
    counts = data["metadata"]["vulnerabilities"]
    items = []
    for name, v in data.get("vulnerabilities", {}).items():
        titles = [x["title"] for x in v.get("via", []) if isinstance(x, dict) and x.get("title")]
        fix = v.get("fixAvailable")
        items.append(
            {
                "package": name,
                "severity": v["severity"],
                "range": v.get("range", ""),
                "title": "; ".join(titles)[:240]
                or "via " + ", ".join(x for x in v.get("via", []) if isinstance(x, str)),
                "fix": fix.get("version", "yes") if isinstance(fix, dict) else ("yes" if fix else "no"),
            }
        )
    items.sort(key=lambda i: NPM_LEVELS.index(i["severity"]) if i["severity"] in NPM_LEVELS else 9)
    return {
        "vulnerable_packages": len(items),
        "vulnerabilities": counts.get("total", len(items)),
        "critical": counts.get("critical", 0),
        "high": counts.get("high", 0),
        "moderate": counts.get("moderate", 0),
        "items": items,
    }


def collect(ctx: Context) -> dict:
    """Audit the target's dependencies."""
    return collect_python(ctx) if ctx.target.kind == "python" else collect_typescript(ctx)
