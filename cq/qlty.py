"""Running qlty and reading its SARIF, shared by the radarlint, smells and scripts collectors.

qlty reads the repo's own `.qlty/qlty.toml`, where the plugin versions and the smell thresholds are
pinned, so the nightly trend and the PR gate measure with the same rules (`cq init` writes cq's
default when the repo has none). The CLI version is pinned by the cq release (cq/config.py PINS):
another version is a tool error, not a silent change.
"""

import json
import re
import shutil
from fnmatch import fnmatch
from pathlib import Path

from cq.context import Context
from cq.proc import ToolError, run
from cq.toolconfig import qlty_config

# Test files: qlty's own test_patterns don't apply to explicit paths, so they are dropped here.
TEST_PATTERNS = ("*/tests/*", "*/test/*", "*.test.*", "*.spec.*", "*/test_*.py", "*_test.py")
# Today's config, also for an old revision's worktree: a backfill measures with the current rules.
CONFIG_FILES = ("qlty.toml", "configs")
SEVERITY = {"error": "warning", "warning": "warning", "note": "info", "none": "info"}
VERSION = re.compile(r"qlty (\S+)")


def check_version(ctx: Context) -> None:
    """Fail loudly when qlty is missing or not the pinned version."""
    if shutil.which("qlty") is None:
        raise ToolError("qlty is not installed: `cq setup` installs it (GitHub release, checksum + attestation)")
    match = VERSION.search(run(["qlty", "--version"], ctx.checkout))
    found = match.group(1) if match else "unknown"
    if found != ctx.settings.qlty:
        raise ToolError(
            f"qlty {found} installed, {ctx.settings.qlty} pinned by this cq release: `cq setup` installs it"
        )
    if qlty_config(ctx.settings) is None:
        raise ToolError("the repo has no .qlty/qlty.toml: `cq init` writes cq's default")


def use_current_config(checkout: Path, source: Path) -> None:
    """Put the current `.qlty/` config into the checkout, so every revision is measured alike."""
    if checkout.resolve() == source.resolve():
        return
    for name in CONFIG_FILES:
        src, dst = source / ".qlty" / name, checkout / ".qlty" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not src.exists():  # configs/ is optional (cq init writes only qlty.toml): mirror its absence
            if dst.is_dir():
                shutil.rmtree(dst)
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)  # a mirror: files today's config dropped go too
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def is_test(path: str) -> bool:
    """Whether a repo-relative path is a test file."""
    return any(fnmatch(path, pattern) or fnmatch(f"x/{path}", pattern) for pattern in TEST_PATTERNS)


def results(sarif: dict) -> list[dict]:
    """SARIF results as flat rows: plugin, rule, severity, file, line, message, properties."""
    rows = []
    for run_ in sarif.get("runs", []):
        for r in run_.get("results", []):
            plugin, _, rule = r.get("ruleId", "unknown").partition(":")
            location = r["locations"][0]["physicalLocation"]  # qlty locates every result in a file
            region = location.get("region") or {}  # SARIF leaves it out for a finding about a whole file
            rows.append(
                {
                    "plugin": plugin,
                    "rule": rule or plugin,
                    "severity": SEVERITY.get(r.get("level", "warning"), "warning"),
                    "file": location["artifactLocation"]["uri"],
                    "line": region.get("startLine"),
                    "end_line": region.get("endLine"),
                    "msg": r["message"]["text"][:240],
                    "properties": r.get("properties", {}),
                }
            )
    return rows


def production(rows: list[dict]) -> list[dict]:
    """The rows outside test files."""
    return [r for r in rows if not is_test(r["file"])]


def items(ctx: Context, rows: list[dict], *fields: str) -> list[dict]:
    """The first 120 rows as report items (file, line, code, message, and ``fields``), each fingerprinted."""
    return ctx.with_fingerprints(
        [
            {"file": r["file"], "line": r["line"], "code": r["rule"], "msg": r["msg"], **{f: r[f] for f in fields}}
            for r in rows[:120]
        ]
    )


def run_sarif(ctx: Context, args: list[str], name: str) -> list[dict]:
    """One qlty command with ``--sarif`` over repo-relative paths; the raw SARIF goes to the run folder."""
    check_version(ctx)
    use_current_config(ctx.checkout, ctx.settings.repo_root)
    out = run(["qlty", *args, "--sarif"], ctx.checkout, ok_codes=(0, 1))
    (ctx.raw_dir / f"{name}.sarif").write_text(out)
    try:
        return results(json.loads(out))
    except json.JSONDecodeError as exc:
        raise ToolError(f"qlty {' '.join(args[:2])} printed no SARIF: {out[-300:]}") from exc
    except KeyError as exc:  # a result without the field qlty always writes: the gate lists it as a tool error
        raise ToolError(f"qlty {' '.join(args[:2])} SARIF has a result without {exc}") from exc


def source_args(ctx: Context) -> list[str]:
    """The target's production paths, relative to the checkout (qlty takes paths, not --all)."""
    return [str(Path(p).relative_to(ctx.checkout)) for p in ctx.sources]
