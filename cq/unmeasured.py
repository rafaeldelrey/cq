"""Apps below a repo's root that detected targets leave out, for a one-line hint.

With no ``targets`` in its settings, cq measures the root only (``config.detect_targets``). Finding
a repo's apps is the ``/setup-cq`` skill's job; this only notices that there are some to find: the
tracked ``pyproject.toml`` files, and ``package.json`` files of TypeScript apps, below the root.
Test and fixture folders don't count. Nothing here changes what a run measures.
"""

import json
from pathlib import Path, PurePosixPath

from cq.config import Settings
from cq.proc import run

MANIFESTS = ("pyproject.toml", "package.json")
NOT_APPS = {"tests", "test", "fixtures"}  # a manifest there is test data, not an app
SHOWN = 3  # folders named in the hint
DEPENDENCIES = ("dependencies", "devDependencies")


def is_typescript(package_json: Path) -> bool:
    """A package.json of a TypeScript app: typescript among its dependencies, or a tsconfig beside it."""
    if (package_json.parent / "tsconfig.json").exists():
        return True
    try:
        data = json.loads(package_json.read_text())
    # A specific, expected exception: a package.json cq can't read (missing in a sparse checkout,
    # not UTF-8, not JSON) is no app cq could measure, and a hint must never stop a run.
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return any(isinstance(deps, dict) and "typescript" in deps for deps in (data.get(k) for k in DEPENDENCIES))


def app_folders(repo_root: Path) -> list[str]:
    """The folders below the root holding a tracked app manifest, sorted; none outside a git repo."""
    if not (repo_root / ".git").exists():
        return []
    tracked = run(["git", "ls-files", "-z", "--", *(f"*{m}" for m in MANIFESTS)], repo_root).split("\0")
    folders = set()
    for name in tracked:
        path = PurePosixPath(name)
        if path.name not in MANIFESTS or len(path.parts) < 2 or NOT_APPS & set(path.parts):
            continue
        if path.name == "package.json" and not is_typescript(repo_root / path):
            continue
        folders.add(str(path.parent))
    return sorted(folders)


def hint(settings: Settings) -> str | None:
    """One line when the targets are detected and apps below the root go unmeasured; else None."""
    if not settings.targets_detected:
        return None
    folders = app_folders(settings.repo_root)
    if not folders:
        return None
    named = ", ".join(folders[:SHOWN]) + (f" and {len(folders) - SHOWN} more" if len(folders) > SHOWN else "")
    return (
        f"Not measured: {len(folders)} app folder(s) below the root ({named}). Targets are detected from "
        "the root only; list them with /setup-cq, or as [[targets]] in cq's settings."
    )
