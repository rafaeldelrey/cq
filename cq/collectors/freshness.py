"""Dependency freshness: how far the app's direct dependencies lag their latest releases.

- ``outdated``: direct dependencies with a newer release.
- ``major_behind``: those whose latest release has a higher major version (likely breaking).
- ``libyear``: the sum, over direct dependencies, of the time between the installed release and
  the latest one, in years (libyear.com). One number for "how stale", that only goes down by
  upgrading; /uv-upgrade is what lowers it.
- ``unpinned`` (requirements.txt apps only): dependencies with no exact ``==`` pin, so no installed
  version to compare. They add nothing to the other three.

Needs network: PyPI's JSON API and the npm registry.
"""

import json
import re
import urllib.request
from datetime import datetime

from packaging.version import InvalidVersion, Version

from cq import requirements
from cq.config import PYPROJECT
from cq.context import Context
from cq.proc import has_lock, run

KINDS = ("python", "typescript")

UV_OUTDATED = re.compile(
    r"[├└]── (?P<name>[\w.\-\[\]]+) v(?P<current>\S+)(?: \([^)]*\))*? \(latest: v(?P<latest>[^)]+)\)"
)
YEAR = 365.25 * 86400
UV_TREE = ["uv", "tree", "--outdated", "--depth", "1", "--frozen"]
# The installed versions come from the lock, and cq doesn't write one into the user's repo (#53).
SKIP_NO_LOCK = "no uv.lock: freshness reads the locked versions, and cq writes no lock into the repo"


def major(version: str) -> int | None:
    """The leading number of a version string."""
    m = re.match(r"\D*(\d+)", version)
    return int(m.group(1)) if m else None


def pypi_project(name: str) -> dict:
    """A package's metadata and releases, from PyPI's JSON API."""
    url = f"https://pypi.org/pypi/{name.split('[')[0]}/json"
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.load(resp)


def release_dates(project: dict) -> dict[str, datetime]:
    """Release → earliest upload time, from a package's PyPI metadata."""
    return {
        version: min(datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00")) for f in files)
        for version, files in project["releases"].items()
        if files
    }


def pypi_dates(name: str) -> dict[str, datetime]:
    """Release → earliest upload time, from PyPI's JSON API."""
    return release_dates(pypi_project(name))


def npm_dates(ctx: Context, name: str) -> dict[str, datetime]:
    """Release → publish time, from the npm registry."""
    times = json.loads(run(["npm", "view", name, "time", "--json"], ctx.app_dir))
    return {
        v: datetime.fromisoformat(t.replace("Z", "+00:00"))
        for v, t in times.items()
        if v not in ("created", "modified")
    }


def lag_years(dates: dict[str, datetime], current: str, latest: str) -> float:
    """Years between two releases; 0 when either date is unknown."""
    if current in dates and latest in dates:
        return max((dates[latest] - dates[current]).total_seconds() / YEAR, 0.0)
    return 0.0


def summarise(ctx: Context, rows: list[dict]) -> dict:
    """The three numbers, plus the list sorted by lag."""
    rows.sort(key=lambda r: -r["years"])
    return {
        "outdated": len(rows),
        "major_behind": sum(1 for r in rows if r["major"]),
        "libyear": round(sum(r["years"] for r in rows), 2),
        "items": ctx.cap(rows, 60),
    }


def outdated_row(name: str, current: str, latest: str, dates: dict[str, datetime], key: str = "") -> dict:
    """One dependency behind its latest release; ``key`` is the release ``current`` names on PyPI."""
    return {
        "package": name,
        "current": current,
        "latest": latest,
        "major": (major(latest) or 0) > (major(current) or 0),
        "years": round(lag_years(dates, key or current, latest), 2),
    }


def parsed(version: str) -> Version | None:
    """A PEP 440 version, or None for a string that isn't one."""
    try:
        return Version(version)
    # A specific, expected exception: a non-PEP 440 version string, compared as text instead.
    except InvalidVersion:
        return None


def release_key(dates: dict[str, datetime], version: str) -> str:
    """The PyPI release a pin names: ``2.31`` pins the release PyPI lists as ``2.31.0``."""
    if version in dates:
        return version
    wanted = parsed(version)
    return next((v for v in dates if wanted is not None and parsed(v) == wanted), version)


def is_behind(pin: str, latest: str) -> bool:
    """Whether a pin is older than the latest release, by version order (a later backport is still older)."""
    pinned, newest = parsed(pin), parsed(latest)
    if pinned is None or newest is None:
        return pin != latest
    return pinned < newest  # a pre-release newer than the latest isn't behind


def pinned_row(name: str, pin: str) -> dict | None:
    """One pinned requirement against PyPI's latest release; None when the pin is not behind it."""
    project = pypi_project(name)
    latest = project["info"]["version"]
    if not is_behind(pin, latest):
        return None
    dates = release_dates(project)
    return outdated_row(name, pin, latest, dates, release_key(dates, pin))


def collect_requirements(ctx: Context) -> dict:
    """The exact pins in the app's requirements files against PyPI; the other lines counted as unpinned."""
    pins: dict[str, str] = {}
    unpinned: set[str] = set()
    for path in requirements.files(ctx.app_dir):
        for req in requirements.parse(path):
            if req.version is None:
                unpinned.add(req.name)
            else:
                pins.setdefault(req.name, req.version)
    unpinned -= pins.keys()  # pinned in one file and left open in another: the pin is what installs
    rows = [row for name, pin in pins.items() if (row := pinned_row(name, pin))]
    return {**summarise(ctx, rows), "unpinned": len(unpinned), "unpinned_packages": sorted(unpinned)}


def collect_python(ctx: Context) -> dict:
    """`uv tree --outdated` for the direct dependencies, release dates from PyPI."""
    if requirements.is_requirements_app(ctx.app_dir):
        return collect_requirements(ctx)
    if not (ctx.app_dir / PYPROJECT).exists():
        return {"skipped": requirements.SKIP_NO_MANIFEST}
    if not has_lock(ctx.app_dir):
        return {"skipped": SKIP_NO_LOCK}
    out = run(UV_TREE, ctx.app_dir)
    (ctx.raw_dir / "uv-outdated.txt").write_text(out)
    rows = []
    for m in UV_OUTDATED.finditer(out):
        name, current, latest = m["name"], m["current"], m["latest"]
        rows.append(outdated_row(name, current, latest, pypi_dates(name)))
    return summarise(ctx, rows)


def collect_typescript(ctx: Context) -> dict:
    """`npm outdated` for the direct dependencies, release dates from the npm registry."""
    out = run(["npm", "outdated", "--json"], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "npm-outdated.json").write_text(out)
    data = json.loads(out or "{}")
    rows = []
    for name, info in data.items():
        current, latest = info.get("current"), info.get("latest")
        if not current or not latest or current == latest:
            continue
        rows.append(outdated_row(name, current, latest, npm_dates(ctx, name)))
    return summarise(ctx, rows)


def collect(ctx: Context) -> dict:
    """Freshness of the target's direct dependencies."""
    return collect_python(ctx) if ctx.target.kind == "python" else collect_typescript(ctx)
