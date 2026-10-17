"""Dependency hygiene: deptry for Python (declared vs imported), knip for the UI (unused files,
exports, dependencies).

deptry codes: DEP001 imported but not declared (works only because something else pulls it in),
DEP002 declared but never imported, DEP003 imported transitive dependency, DEP004 dev dependency
used in production code.
"""

import json

from cq.context import Context
from cq.proc import run, uv_app

KINDS = ("python", "typescript")

DEPTRY_LABELS = {
    "DEP001": "missing",
    "DEP002": "unused",
    "DEP003": "transitive",
    "DEP004": "dev_in_prod",
}


def first_party_packages(ctx: Context) -> list[str]:
    """The app's own top-level packages: a source dir that is a package, or the packages inside a src/ dir."""
    names = []
    for path in ctx.sources:
        if path.is_file():
            names.append(path.stem)
        elif (path / "__init__.py").exists():
            names.append(path.name)
        else:
            names.extend(child.name for child in path.iterdir() if (child / "__init__.py").exists())
    return names


def collect_python(ctx: Context) -> dict:
    """deptry over the app, with the app's own pyproject declaring the dependencies."""
    out = ctx.raw_dir / "deptry.json"
    # Inside the app's own environment: deptry maps import names to distributions (sklearn →
    # scikit-learn) from the installed metadata. The dev extra is dev-only, not unused.
    first_party = [arg for name in first_party_packages(ctx) for arg in ("--known-first-party", name)]
    ignores = ["--per-rule-ignores", ",".join(ctx.target.deptry_ignore)] if ctx.target.deptry_ignore else []
    run(
        uv_app("deptry", ".", "--json-output", str(out), "--pep621-dev-dependency-groups", "dev", *first_party, *ignores,
               overlay=("deptry==0.25.1",)),
        ctx.app_dir,
        ok_codes=(0, 1),
    )  # fmt: skip
    rows = json.loads(out.read_text())
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


def collect_typescript(ctx: Context) -> dict:
    """knip with its Next.js plugin; it reads package.json and the tsconfig."""
    out = run(["npx", "-y", ctx.settings.knip, "--reporter", "json", "--no-progress"], ctx.app_dir, ok_codes=(0, 1))
    (ctx.raw_dir / "knip.json").write_text(out)
    data = json.loads(out)
    counts: dict[str, int] = {}
    items = []
    for f in data.get("issues", []):
        for kind, entries in f.items():
            if kind == "file" or not isinstance(entries, list) or not entries:
                continue
            counts[kind] = counts.get(kind, 0) + len(entries)
            for e in entries:
                name = e.get("name", "") if isinstance(e, dict) else str(e)
                items.append(
                    {
                        "file": ctx.rel(f["file"]),
                        "kind": kind,
                        "name": name,
                        "line": e.get("line") if isinstance(e, dict) else None,
                    }
                )
    files = data.get("files", [])
    if files:
        counts["files"] = counts.get("files", 0) + len(files)
        items.extend({"file": ctx.rel(f), "kind": "files", "name": "unused file", "line": None} for f in files)
    return {"issues": sum(counts.values()), "by_kind": counts, "items": items[:120]}


def collect(ctx: Context) -> dict:
    """Check the target's dependency declarations against its imports."""
    return collect_python(ctx) if ctx.target.kind == "python" else collect_typescript(ctx)
