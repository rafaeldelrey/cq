"""Shell scripts (shellcheck) and GitHub workflows (actionlint, zizmor) across the repository.

Shell scripts often run the checks, deploys and backups, and rarely have tests. shellcheck catches the quoting, word-splitting and exit-code bugs
that bite shell. Only git-tracked scripts under ``shell_roots`` are checked (not vendored code).
actionlint checks the workflows' syntax; zizmor (through qlty) their security: actions not pinned
to a commit, credentials left in the workspace, script injection.
"""

import json
import shutil

from cq import qlty
from cq.context import Context
from cq.proc import ToolError, run

KINDS = ("repo",)

SHELLCHECK_LEVELS = ("error", "warning", "info", "style")


def tracked_scripts(ctx: Context) -> list[str]:
    """Repo-relative paths of the tracked ``*.sh`` files under the configured roots."""
    # :(glob) makes ** match zero directories too, so scripts/a.sh is included, not only scripts/x/y.sh.
    out = run(
        [
            "git",
            "ls-files",
            "--",
            *(":(glob)**/*.sh" if root in (".", "") else f":(glob){root}/**/*.sh" for root in ctx.settings.shell_roots),
        ],
        ctx.checkout,
    )
    return sorted({line for line in out.splitlines() if line})


def shellcheck(ctx: Context) -> dict:
    """shellcheck over the tracked scripts, by level."""
    files = tracked_scripts(ctx)
    if not files:
        return {"files": 0, "findings": 0, "items": []}
    out = run(["shellcheck", "-f", "json1", "-x", *files], ctx.checkout, ok_codes=(0, 1))
    (ctx.raw_dir / "shellcheck.json").write_text(out)
    comments = json.loads(out)["comments"]
    counts = {level: sum(1 for c in comments if c["level"] == level) for level in SHELLCHECK_LEVELS}
    return {
        "files": len(files),
        "findings": len(comments),
        **counts,
        "items": [
            {
                "file": c["file"],
                "line": c["line"],
                "code": f"SC{c['code']}",
                "level": c["level"],
                "msg": c["message"][:200],
            }
            for c in ctx.cap(sorted(comments, key=lambda c: SHELLCHECK_LEVELS.index(c["level"])), 80, "shell.items")
        ],
    }


def actionlint(ctx: Context) -> dict:
    """actionlint over .github/workflows (it finds them itself)."""
    if not (ctx.checkout / ".github" / "workflows").is_dir():
        return {"skipped": "no workflows"}
    out = run(["actionlint", "-format", "{{json .}}"], ctx.checkout, ok_codes=(0, 1))
    (ctx.raw_dir / "actionlint.json").write_text(out)
    rows = json.loads(out or "[]")
    return {
        "findings": len(rows),
        "items": [
            {"file": r["filepath"], "line": r["line"], "code": r["kind"], "msg": r["message"][:200]}
            for r in ctx.cap(rows, 50, "workflows.items")
        ],
    }


def zizmor(ctx: Context) -> dict:
    """zizmor over .github/workflows, through qlty (version pinned in .qlty/qlty.toml)."""
    if not (ctx.checkout / ".github" / "workflows").is_dir():
        return {"skipped": "no workflows"}
    rows = qlty.run_sarif(ctx, ["check", "--no-fail", "--filter", "zizmor", ".github/workflows"], "zizmor")
    return {
        "findings": len(rows),
        "items": ctx.with_fingerprints(
            [
                {"file": r["file"], "line": r["line"], "code": r["rule"], "msg": r["msg"]}
                for r in ctx.cap(rows, 50, "workflow_security.items")
            ]
        ),
    }


def collect(ctx: Context) -> dict:
    """The three linters; a missing binary is an error (a blind spot), not a clean result."""
    for tool in ("shellcheck", "actionlint"):
        if shutil.which(tool) is None:
            raise ToolError(f"{tool} is not installed (brew install {tool})")
    return {"shell": shellcheck(ctx), "workflows": actionlint(ctx), "workflow_security": zizmor(ctx)}
