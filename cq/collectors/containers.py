"""Containers: the app's Dockerfile (hadolint) and its published image (Trivy).

pip-audit and npm audit see the app's own dependencies; Trivy also sees the image's operating
system packages (Debian, Alpine) and whatever else the build put in. The image is the one the
target's ``image`` names (the tag build-and-push.sh pushes), so it is the deployed build, not the
measured commit: an old commit (a backfill) skips the image scan rather than judge it by today's.
hadolint lints the Dockerfile at the measured commit.
"""

import json
import shutil
from datetime import UTC, datetime, timedelta

from cq.context import Context
from cq.proc import ToolError, run

KINDS = ("python", "typescript")

TRIVY_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
IMAGE_MAX_AGE = timedelta(days=7)  # older commits don't get today's image judged against them


def hadolint(ctx: Context) -> dict:
    """Dockerfile findings by level."""
    dockerfile = ctx.app_dir / "Dockerfile"
    if not dockerfile.exists():
        return {"skipped": "no Dockerfile"}
    out = run(["hadolint", "--format", "json", "--no-fail", str(dockerfile)], ctx.checkout, ok_codes=(0, 1))
    (ctx.raw_dir / "hadolint.json").write_text(out)
    rows = json.loads(out or "[]")
    return {
        "findings": len(rows),
        "errors": sum(1 for r in rows if r["level"] == "error"),
        "warnings": sum(1 for r in rows if r["level"] == "warning"),
        "items": [
            {
                "file": ctx.rel(r["file"], ctx.checkout),
                "line": r["line"],
                "code": r["code"],
                "level": r["level"],
                "msg": r["message"][:200],
            }
            for r in rows
        ],
    }


def trivy(ctx: Context) -> dict:
    """Known vulnerabilities in the published image, by severity."""
    image = ctx.target.image
    if not image:
        return {"skipped": "no image configured"}
    head = datetime.fromisoformat(run(["git", "show", "-s", "--format=%cI", "HEAD"], ctx.checkout).strip())
    if datetime.now(UTC) - head > IMAGE_MAX_AGE:
        return {"skipped": f"commit older than {IMAGE_MAX_AGE.days} days: today's image says nothing about it"}
    out_file = ctx.raw_dir / "trivy.json"
    run(
        ["trivy", "image", "--quiet", "--scanners", "vuln", "--format", "json", "--output", str(out_file), image],
        ctx.checkout,
    )
    data = json.loads(out_file.read_text())
    vulns = [v for r in data.get("Results", []) for v in (r.get("Vulnerabilities") or [])]
    counts = {level.lower(): sum(1 for v in vulns if v.get("Severity") == level) for level in TRIVY_LEVELS}
    fixable = [v for v in vulns if v.get("FixedVersion") and v.get("Severity") in ("CRITICAL", "HIGH")]
    seen: set[tuple[str, str]] = set()
    items = []
    for v in sorted(vulns, key=lambda v: TRIVY_LEVELS.index(v.get("Severity", "UNKNOWN"))):
        key = (v["PkgName"], v["VulnerabilityID"])
        if key in seen or v.get("Severity") not in ("CRITICAL", "HIGH"):
            continue
        seen.add(key)
        items.append({
            "package": v["PkgName"],
            "version": v.get("InstalledVersion", ""),
            "id": v["VulnerabilityID"],
            "severity": v["Severity"].lower(),
            "fix": v.get("FixedVersion") or "none yet",
            "title": (v.get("Title") or "")[:160],
        })  # fmt: skip
    os_info = data.get("Metadata", {}).get("OS", {})
    return {
        "image": image,
        "digest": (data.get("Metadata", {}).get("RepoDigests") or [""])[0],
        "os": f"{os_info.get('Family', '')} {os_info.get('Name', '')}".strip(),
        **counts,
        "fixable_high": len({(v["PkgName"], v["VulnerabilityID"]) for v in fixable}),
        "items": items[:80],
    }


def collect(ctx: Context) -> dict:
    """hadolint and Trivy; each is skipped (not an error) when the app has nothing for it."""
    for tool in ("hadolint", "trivy"):
        if shutil.which(tool) is None:
            raise ToolError(f"{tool} is not installed (brew install {tool})")
    return {"dockerfile": hadolint(ctx), "image": trivy(ctx)}
