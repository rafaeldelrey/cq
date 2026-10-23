"""Dependency hygiene: deptry for Python (declared vs imported), knip for the UI (unused files,
exports, dependencies).

deptry codes: DEP001 imported but not declared (works only because something else pulls it in),
DEP002 declared but never imported, DEP003 imported transitive dependency, DEP004 dev dependency
used in production code.
"""

import json
from pathlib import Path

from cq.config import PINS, PYPROJECT, has_python
from cq.context import Context
from cq.proc import has_dev_extra, read_report, run, uv_app
from cq.requirements import REQUIREMENTS, SKIP_NO_MANIFEST

KINDS = ("python", "typescript")

DEPTRY_LABELS = {
    "DEP001": "missing",
    "DEP002": "unused",
    "DEP003": "transitive",
    "DEP004": "dev_in_prod",
}


def first_party_packages(ctx: Context) -> list[str]:
    """The app's own top-level packages: a source dir that is a package, or the packages inside a src/ dir.

    Namespace packages (no ``__init__.py``) count too: a folder of Python files is a package by name.
    """
    return [name for path in ctx.sources for name in package_names(path)]


def package_names(path: Path) -> list[str]:
    """The top-level names one source path gives the app: a module, a package, or the packages inside it."""
    if path.is_file():
        return [path.stem]
    if (path / "__init__.py").exists():
        return [path.name]
    if path.name == "src":  # a container: its packages and its direct modules are what imports name
        packages = [c.name for c in sorted(path.iterdir()) if c.is_dir() and has_python(c)]
        return packages + [f.stem for f in sorted(path.glob("*.py"))]
    inner = [c.name for c in sorted(path.iterdir()) if (c / "__init__.py").exists()]
    if inner:
        return inner
    # A folder of modules with no package inside is itself a namespace package (acme/core.py).
    return [path.name] if has_python(path) else []


def collect_python(ctx: Context) -> dict:
    """deptry over the app, with the app's own pyproject (or requirements.txt) declaring the dependencies."""
    if not any((ctx.app_dir / name).exists() for name in (PYPROJECT, REQUIREMENTS)):
        # deptry has nothing to compare the imports with, and would fail (#58): a blind spot that says so.
        return {"skipped": SKIP_NO_MANIFEST}
    out = ctx.raw_dir / "deptry.json"
    # Inside the app's own environment: deptry maps import names to distributions (sklearn →
    # scikit-learn) from the installed metadata. A dev extra is dev-only, not unused; deptry reads a
    # dev dependency group as dev-only by itself.
    first_party = [arg for name in first_party_packages(ctx) for arg in ("--known-first-party", name)]
    ignores = ["--per-rule-ignores", ",".join(ctx.target.deptry_ignore)] if ctx.target.deptry_ignore else []
    dev_extra = ["--pep621-dev-dependency-groups", "dev"] if has_dev_extra(ctx.app_dir) else []
    run(
        uv_app(
            ctx.app_dir,
            "deptry",
            ".",
            "--json-output",
            str(out),
            *dev_extra,
            *first_party,
            *ignores,
            overlay=(PINS["deptry"],),
        ),
        ctx.app_dir,
        ok_codes=(0, 1),
    )
    rows = json.loads(read_report(out, "deptry"))
    counts: dict[str, int] = dict.fromkeys(DEPTRY_LABELS.values(), 0)
    modules: dict[tuple[str, str], dict] = {}
    for r in rows:
        code: str = r["error"]["code"]
        label = DEPTRY_LABELS.get(code, code)
        counts[label] = counts.get(label, 0) + 1
        key = (code, r["module"])
        entry = modules.setdefault(
            key,
            {
                "code": code,
                "module": r["module"],
                "msg": r["error"]["message"],
                "files": 0,
                "file": ctx.rel(r["location"]["file"]),
            },
        )
        entry["files"] += 1
    return {
        "issues": len(modules),
        **counts,
        "items": sorted(modules.values(), key=lambda m: (m["code"], -m["files"])),
    }


def knip_kinds(issue: dict) -> list[tuple[str, list]]:
    """One file's knip issues as (kind, entries), the kinds with something in them."""
    return [
        (kind, entries) for kind, entries in issue.items() if kind != "file" and isinstance(entries, list) and entries
    ]


def knip_item(file: str, kind: str, entry: object) -> dict:
    """One knip entry: a dict with a name and a line, or a bare name."""
    if isinstance(entry, dict):
        return {"file": file, "kind": kind, "name": entry.get("name", ""), "line": entry.get("line")}
    return {"file": file, "kind": kind, "name": str(entry), "line": None}


def collect_typescript(ctx: Context) -> dict:
    """knip with its Next.js plugin; it reads package.json and the tsconfig."""
    out = run(["npx", "-y", ctx.settings.knip, "--reporter", "json", "--no-progress"], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "knip.json").write_text(out)
    data = json.loads(out)
    counts: dict[str, int] = {}
    items = []
    for f in data.get("issues", []):
        for kind, entries in knip_kinds(f):
            counts[kind] = counts.get(kind, 0) + len(entries)
            items.extend(knip_item(ctx.rel(f["file"]), kind, e) for e in entries)
    files = data.get("files", [])
    if files:
        counts["files"] = counts.get("files", 0) + len(files)
        items.extend({"file": ctx.rel(f), "kind": "files", "name": "unused file", "line": None} for f in files)
    return {"issues": sum(counts.values()), "by_kind": counts, "items": ctx.cap(items, 120)}


def collect(ctx: Context) -> dict:
    """Check the target's dependency declarations against its imports."""
    return collect_python(ctx) if ctx.target.kind == "python" else collect_typescript(ctx)
