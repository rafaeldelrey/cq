"""Known vulnerabilities in dependencies: pip-audit on each Python app's lock, npm audit on the UI.

A requirements.txt app has no lock: its requirements are resolved with ``uv pip compile`` into the
run's own folder, then audited the same way.

Both need network (the OSV / PyPI and npm advisory databases). The UI counts production
dependencies only (``--omit=dev``): what ships in the image.
"""

import json
from pathlib import Path

from cq import requirements
from cq.config import PYPROJECT
from cq.context import Context
from cq.proc import ToolError, has_lock, own, read_report, run

KINDS = ("python", "typescript")

NPM_LEVELS = ("critical", "high", "moderate", "low", "info")


def exported_lock(ctx: Context) -> Path:
    """The app's locked requirements, exported from uv.lock (the dev group included, as uv does by default)."""
    req = ctx.raw_dir / "requirements.txt"
    req.write_text(
        run(
            ["uv", "export", "-q", "--frozen", "--no-hashes", "--no-emit-project", "--format", "requirements-txt"],
            ctx.app_dir,
        )
    )
    return req


def resolved_requirements(ctx: Context) -> Path:
    """A requirements.txt app's requirements and dev requirements, resolved to exact versions in the run's folder."""
    req = ctx.raw_dir / "requirements.txt"
    sources = [str(path) for path in requirements.files(ctx.app_dir)]
    run(["uv", "pip", "compile", "-q", "--no-header", "--no-annotate", *sources, "-o", str(req)], ctx.app_dir)
    return req


def pinned_requirements(ctx: Context) -> Path | str:
    """The app's dependencies at exact versions, or why there are none to audit."""
    if requirements.is_requirements_app(ctx.app_dir):
        return resolved_requirements(ctx)
    if not (ctx.app_dir / PYPROJECT).exists():  # a uv.lock alone is no project uv can export
        return requirements.SKIP_NO_MANIFEST
    if not has_lock(ctx.app_dir):
        # No lock to audit, and cq writes none into the user's repo (#53): a blind spot that says so.
        return "no uv.lock to audit, and cq writes no lock into the repo"
    return exported_lock(ctx)


def collect_python(ctx: Context) -> dict:
    """Pin the app's dependencies to exact versions, then audit them without re-resolving."""
    req = pinned_requirements(ctx)
    if isinstance(req, str):
        return {"skipped": req}
    out = ctx.raw_dir / "pip-audit.json"
    run(
        [own("pip-audit"), "-r", str(req), "--no-deps", "--disable-pip", "-f", "json", "-o", str(out)],
        ctx.checkout,
        ok_codes=(0, 1),
    )
    deps = json.loads(read_report(out, "pip-audit"))["dependencies"]
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


def fix_label(fix: object) -> str:
    """npm audit's ``fixAvailable``: the version that fixes it, or whether a fix exists."""
    if isinstance(fix, dict):
        return fix.get("version", "yes")
    return "yes" if fix else "no"


def json_object(data: dict, key: str) -> dict:
    """``data[key]`` when it is a JSON object, else an empty one."""
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def npm_error(data: dict) -> str | None:
    """npm's own message when its audit JSON is an error body rather than a report; None for a report.

    A registry error has a ``statusCode`` and the registry's message (in ``body``, or at the top);
    a local one has ``error.code`` and ``error.summary``. The ``headers`` npm also prints (cookies
    among them) never reach the message.
    """
    if "metadata" in data:
        return None
    body, error = json_object(data, "body"), json_object(data, "error")
    if from_registry(data):
        status = str(data["statusCode"])
        message = body.get("message") or data.get("message") or body.get("error") or "no message"
        return f"{status} {message.removeprefix(status).strip()}"
    if error.get("code") or error.get("summary"):
        return f"{error.get('code', '')} {error.get('summary', '')}".strip()
    # Only the keys of a reply cq doesn't know: its values could hold anything.
    return f"no report and no message (keys: {', '.join(sorted(data))})"


def from_registry(data: dict) -> bool:
    """Whether npm's reply is the registry's error (it carries the HTTP status), which a rerun can clear."""
    return "statusCode" in data and "metadata" not in data


def audit_attempt(ctx: Context) -> dict:
    """One ``npm audit`` of the production tree, its JSON kept in the run's folder.

    A registry error also carries the response's ``headers`` (a ``set-cookie`` among them), which
    are left out of the kept file.
    """
    try:
        out = run(["npm", "audit", "--json", "--omit=dev"], ctx.app_dir, ok_codes=(0, 1))
    # A specific, expected exception: npm exiting otherwise with its JSON reply on stdout goes through
    # the same reading (and retry) as exit 1, and never quotes the reply's headers. Anything else propagates.
    except ToolError as exc:
        if not exc.stdout.lstrip().startswith("{"):
            raise
        out = exc.stdout
    data = json.loads(out)
    kept = json.dumps({k: v for k, v in data.items() if k != "headers"}, indent=2) if "headers" in data else out
    (ctx.raw_dir / "npm-audit.json").write_text(kept)
    return data


def audit_report(ctx: Context) -> dict:
    """npm audit's report. A registry error is tried once more (they have passed on a rerun);
    a local one isn't. Either, left standing, is a tool error with npm's message (#66)."""
    data = audit_attempt(ctx)
    if from_registry(data):
        data = audit_attempt(ctx)
    message = npm_error(data)
    if message is not None:
        raise ToolError(f"npm audit: {message}")
    return data


def collect_typescript(ctx: Context) -> dict:
    """npm audit of the production dependency tree.

    npm audit reads package-lock.json. A pnpm or Yarn app has none, and cq doesn't audit through
    those managers yet, so the app is reported as skipped, with why, rather than as audited or failed.
    """
    if not (ctx.app_dir / "package-lock.json").exists():
        other = next((lock for lock in ("pnpm-lock.yaml", "yarn.lock") if (ctx.app_dir / lock).exists()), None)
        if other:
            return {"skipped": f"{other}: cq audits npm projects only (npm audit needs package-lock.json)"}
    data = audit_report(ctx)
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
                "fix": fix_label(fix),
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
