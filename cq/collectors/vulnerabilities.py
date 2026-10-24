"""Known vulnerabilities in dependencies: pip-audit on each Python app's lock, npm audit on the UI.

A requirements.txt app has no lock: its requirements are resolved with ``uv pip compile`` into the
run's own folder, then audited the same way.

Both need network (the OSV / PyPI and npm advisory databases). The UI counts production
dependencies only (``--omit=dev``): what ships in the image.
"""

import json
import re
from pathlib import Path

from cq import requirements
from cq.config import PYPROJECT
from cq.context import Context
from cq.proc import ToolError, has_dev_extra, has_lock, own, read_report, run

KINDS = ("python", "typescript")

AUDITED = "requirements.txt"  # in the run's folder: the exact versions pip-audit checks

NPM_LEVELS = ("critical", "high", "moderate", "low", "info")


def exported_lock(ctx: Context) -> Path:
    """The app's locked requirements, exported from uv.lock (the dev group included, as uv does by default)."""
    req = ctx.raw_dir / AUDITED
    req.write_text(
        run(
            ["uv", "export", "-q", "--frozen", "--no-hashes", "--no-emit-project", "--format", "requirements-txt"],
            ctx.app_dir,
        )
    )
    return req


def resolved_requirements(ctx: Context) -> Path:
    """A requirements.txt app's requirements and dev requirements, resolved to exact versions in the run's folder."""
    req = ctx.raw_dir / AUDITED
    sources = [str(path) for path in requirements.files(ctx.app_dir)]
    run(["uv", "pip", "compile", "-q", "--no-header", "--no-annotate", *sources, "-o", str(req)], ctx.app_dir)
    return req


# A resolved `name==version` line; a path or VCS dependency (`../libs/x`, `x @ git+…`) is not on PyPI.
PINNED_LINE = re.compile(r"^[A-Za-z0-9][\w.\-]*(?:\[[^\]]*\])?==")


NO_BUILD = "is disabled"  # uv: "Building source distributions for `<name>` is disabled"


def compiled_project(ctx: Context) -> Path | str:
    """An unlocked uv app's dependencies, resolved by ``uv pip compile`` in its folder, into the run's folder.

    Run there, uv applies the app's own settings (its uv.toml, its or a parent's [tool.uv], its
    sources), as `uv lock` would. The dev group comes too, as `uv export` includes it, and so does a
    dev extra. A path or workspace dependency is never built: its build would write into the repo,
    so one that needs a build makes the app skipped, with the reason.
    """
    req = ctx.raw_dir / AUDITED
    group = ["--group", "dev"] if requirements.has_dev_group(ctx.app_dir) else []
    extra = ["--extra", "dev"] if has_dev_extra(ctx.app_dir) else []
    no_build = [arg for name in sorted(requirements.in_repo_sources(ctx.app_dir)) for arg in ("--only-binary", name)]
    cmd = ["uv", "pip", "compile", "-q", "--no-header", "--no-annotate", PYPROJECT, *group, *extra, *no_build]
    try:
        run([*cmd, "-o", str(req)], ctx.app_dir)
    # A specific, expected exception: uv refusing to build a dependency from the repo; anything else propagates.
    except ToolError as exc:
        if not no_build or NO_BUILD not in exc.stderr:
            raise
        return requirements.SKIP_LOCAL_BUILD
    return req


def keep_pinned(req: Path) -> int:
    """Keep only the file's `name==version` requirements for pip-audit; how many others (path, VCS) it dropped.

    Requirements, not lines: with uv's `generate-hashes`, each pin continues over `\\` lines of hashes.
    """
    lines = requirements.logical_lines(req.read_text())
    pinned = [line for line in lines if PINNED_LINE.match(line)]
    req.write_text("".join(f"{line}\n" for line in pinned))
    return len(lines) - len(pinned)


def pinned_requirements(ctx: Context) -> Path | str:
    """The app's dependencies at exact versions, or why there are none to audit."""
    if requirements.is_requirements_app(ctx.app_dir):
        return resolved_requirements(ctx)
    if not (ctx.app_dir / PYPROJECT).exists():  # a uv.lock alone is no project uv can export
        return requirements.SKIP_NO_MANIFEST
    if not has_lock(ctx.app_dir):  # cq writes no lock into the repo (#53): resolve one in the run's folder
        if requirements.dynamic_dependencies(ctx.app_dir):
            return requirements.SKIP_DYNAMIC
        return compiled_project(ctx)
    return exported_lock(ctx)


def distinct_advisories(vulns: list[dict]) -> list[dict]:
    """One entry per advisory: pip-audit 2.10 lists some twice for the same package (#69).

    Entries whose IDs and aliases meet, directly or through others, are one advisory: the same ID,
    or a PyPI advisory and the GitHub or CVE ID it aliases. Each group keeps its first entry's ID
    and every fix version any of its entries names, whatever order pip-audit lists them in.
    """
    groups: list[tuple[set[str], dict]] = []
    for vuln in vulns:
        names = {vuln["id"], *vuln.get("aliases", [])}
        fixes = set(vuln.get("fix_versions", []))
        touching = [g for g in groups if not g[0].isdisjoint(names)]
        if not touching:
            groups.append((names, {**vuln, "fix_versions": fixes}))
            continue
        first = touching[0]
        for other in touching[1:]:
            first[0].update(other[0])
            fixes |= set(other[1]["fix_versions"])
            groups.remove(other)
        first[0].update(names)
        first[1]["fix_versions"] = first[1]["fix_versions"] | fixes
    return [{**entry, "fix_versions": sorted(entry["fix_versions"])} for _, entry in groups]


def collect_python(ctx: Context) -> dict:
    """Pin the app's dependencies to exact versions, then audit them without re-resolving."""
    req = pinned_requirements(ctx)
    if isinstance(req, str):
        return {"skipped": req}
    # A path or VCS dependency isn't on PyPI: counted, so an app with only those isn't read as clean.
    unaudited = keep_pinned(req)
    out = ctx.raw_dir / "pip-audit.json"
    run(
        [own("pip-audit"), "-r", str(req), "--no-deps", "--disable-pip", "-f", "json", "-o", str(out)],
        ctx.checkout,
        ok_codes=(0, 1),
    )
    deps = json.loads(read_report(out, "pip-audit"))["dependencies"]
    vulnerable = [{**d, "vulns": distinct_advisories(d["vulns"])} for d in deps if d.get("vulns")]
    return {
        "packages": len(deps),
        "unaudited": unaudited,
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
