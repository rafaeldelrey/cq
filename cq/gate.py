"""The PR gate (`cq gate`): what a branch adds, judged against its base.

Four sources of findings, each one new on the branch by construction:

- **Line level.** ``qlty check`` and ``qlty smells`` with ``--upstream``: only the lines the branch
  changed (ruff, radarlint, bandit, shellcheck, hadolint, zizmor, osv-scanner; parameters, returns,
  nesting, boolean logic, copies). trufflehog is skipped: betterleaks below covers secrets.
- **Compared.** The tools without a line-level mode (aislop, vale, pyscn, fallow, semgrep with the
  house rules) run on the branch and on its base. A finding is new when its stable key is not in
  the base's, the same key logic as ``findings.jsonl``, and it lies in a file the branch changed.
- **Secrets.** betterleaks over the branch's commits (``<base>..HEAD``).
- **Ratchets**, which may not get worse than the base: suppressions, cognitive complexity (no new
  function over the limit, none over it growing), broken import contracts, deptry issues, and
  critical or high vulnerabilities when a lockfile changed.

The mode and the blocking list live in the settings' ``gate`` table. In ``warn`` the gate always
exits 0, whatever it finds and whichever tool failed. In ``block`` it exits 1 when a blocking finding is
new or a tool did not run (a blind spot is not a pass). A crash of the gate itself (git, a bug) is
not a finding: it fails the job in either mode, to be fixed. actionlint is not a qlty plugin here;
check.sh's CI run and the nightly scripts collector cover workflow syntax. Output: ``runs/<id>/gate-findings.jsonl``, ``gate.md``, and the same Markdown
appended to ``$GITHUB_STEP_SUMMARY`` when CI sets it.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path

from cq import qlty
from cq.collectors import secrets
from cq.collectors.vale import EXTENSIONS
from cq.compare import COMPARED, base_errors, new_compared, partial_scans, ratchets, renamed
from cq.config import GateSettings, Settings, Target, ensure_data_dir
from cq.context import Context
from cq.diff import changed_lines, diff_lines, diff_status, overlaps
from cq.finding import Finding, Source
from cq.findings import write_jsonl
from cq.proc import ToolError
from cq.runner import git, snapshot
from cq.toolconfig import gitleaks_config

RATCHETS = ("suppressions", "cognitive", "imports", "dependencies")
LOCKFILES = ("uv.lock", "package-lock.json")
SHOWN = 50  # rows per table in the summary; the JSONL has them all
# qlty smells --upstream works per changed file, not per line: a smell counts only when its span
# overlaps a changed line. file-complexity spans the whole file, so any edit would hit it; growth
# in complexity is the cognitive ratchet's and check.sh's CC gate's job.
WHOLE_FILE_SMELLS = {"file-complexity"}


def affected(targets: tuple[Target, ...], changed: list[str]) -> list[str]:
    """The targets the branch touches; the repo target (vale) when a doc or a source file changed."""
    names = []
    for t in targets:
        repo_hit = t.kind == "repo" and any(f.endswith(EXTENSIONS) for f in changed)
        if repo_hit or (t.kind != "repo" and any(t.owns(f) for f in changed)):
            names.append(t.name)
    return names


def lock_changed(targets: tuple[Target, ...], changed: list[str]) -> set[str]:
    """The targets whose lockfile the branch changed: only they get the vulnerability ratchet."""
    return {t.name for t in targets if t.kind != "repo" and any(t.repo_path(lock) in changed for lock in LOCKFILES)}


def line_level(ctx: Context, base_sha: str) -> list[Finding]:
    """qlty check and qlty smells on the lines the branch changed."""
    lines = changed_lines(base_sha)
    check = qlty.run_sarif(
        ctx, ["check", "--upstream", base_sha, "--skip", "trufflehog", "--no-fail", "--no-progress"], "gate-check"
    )
    smells = qlty.run_sarif(ctx, ["smells", "--upstream", base_sha, "--quiet"], "gate-smells")
    found = [
        Source("repo", "qlty").finding(f"{r['plugin']}:{r['rule']}", r["severity"], r["msg"], r["file"], r["line"])
        for r in check
    ]
    found += [
        Source("repo", "smells").finding(r["rule"], "warning", r["msg"], r["file"], r["line"])
        for r in smells
        if not qlty.is_test(r["file"]) and r["rule"] not in WHOLE_FILE_SMELLS and overlaps(r, lines)
    ]
    return found


def branch_secrets(settings: Settings, base_sha: str) -> list[Finding]:
    """betterleaks over the branch's own commits; only rule, file, line and commit are kept."""
    rows = secrets.betterleaks(
        ["git", ".", f"--log-opts={base_sha}..HEAD"], settings.repo_root, gitleaks_config(settings)
    )
    return [
        Source("repo", "secrets").finding(
            f"in-branch-{r['rule']}",
            "error",
            f"{r['rule']} committed on this branch (commit {r['commit']}): drop it from the branch and rotate it",
            r["file"],
            r["line"],
        )
        for r in rows
    ]


def blocks(f: Finding, patterns: tuple[str, ...]) -> bool:
    """Whether a new finding blocks: every error, and the warnings the blocking list names."""
    return f["severity"] == "error" or any(fnmatch(f"{f['tool']}:{f['rule']}", p) for p in patterns)


def cell(text: object) -> str:
    """Text safe in a Markdown table cell."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")[:200]


def table(findings: list[Finding]) -> list[str]:
    """A Markdown table of findings, the first SHOWN of them."""
    rows = ["| Tool | Rule | Where | Message |", "|---|---|---|---|"]
    for f in findings[:SHOWN]:
        where = f"{f['file']}:{f['line']}" if f.get("line") else f["file"] or f["target"]
        rows.append(f"| {f['tool']} | {cell(f['rule'])} | {cell(where)} | {cell(f['message'])} |")
    if len(findings) > SHOWN:
        rows.append(f"\n…and {len(findings) - SHOWN} more in `gate-findings.jsonl` (the run artifact).")
    return rows


def summary(findings: list[Finding], errors: dict[str, str], info: dict) -> str:
    """The gate's Markdown summary for the job page."""
    blocking = [f for f in findings if f["blocking"]]
    others = [f for f in findings if not f["blocking"]]
    verdict = "would fail" if info["mode"] == "warn" else "fails"
    lines = [
        f"## Quality gate ({info['mode']} mode)",
        "",
        f"`{info['head']}` against `{info['base']}`: {info['changed']} changed files"
        f" ({', '.join(info['targets']) or 'no app'}).",
        "",
        f"**{len(blocking)} blocking** · {len(others)} other new findings · {len(errors)} tool errors",
        "",
    ]
    if blocking or errors:
        lines += [f"The gate {verdict}: fix these, or say in the PR why they stay.", ""]
    if blocking:
        lines += ["### Blocking", "", *table(blocking), ""]
    if errors:
        lines += ["### Tools that did not run", "", *[f"- `{k}`: {cell(v)}" for k, v in errors.items()], ""]
    if others:
        lines += ["### Other new findings (reported, not blocking)", "", *table(others), ""]
    if not findings and not errors:
        lines += ["Nothing new. ✅", ""]
    return "\n".join(lines)


def attempt(name: str, errors: dict[str, str], step: Callable[..., list[Finding]], *args: object) -> list[Finding]:
    """One step's findings; a ToolError is recorded as a tool error instead of ending the gate."""
    try:
        return step(*args)
    except ToolError as exc:  # explicit degraded mode: the other sources still report, and the
        # error is listed in the summary and blocks in block mode.
        errors[name] = str(exc)
        return []


@dataclass(frozen=True)
class GateOutcome:
    """What one gate run decided: the exit code CI uses, the Markdown summary, and the run folder."""

    exit_code: int
    summary: str
    out_dir: Path


def run_gate(settings: Settings, gate: GateSettings, base: str, *, report_only: bool = False) -> GateOutcome:
    """Run the gate for HEAD against ``base``: the summary is written to the run folder and returned."""
    base_sha = git("merge-base", base, "HEAD")
    head_short = git("rev-parse", "--short", "HEAD")
    changed, touched, renames = diff_status(base_sha)
    names = affected(settings.targets, touched)  # a deletion can break a contract or a dependency
    locks = lock_changed(settings.targets, touched)  # a deleted lockfile is a change too
    out_dir = settings.runs_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_gate_{head_short}"
    ensure_data_dir(settings)
    out_dir.mkdir(parents=True)
    repo = next(t for t in settings.targets if t.kind == "repo")
    ctx = Context(target=repo, checkout=settings.repo_root, raw_dir=out_dir, settings=settings)

    errors: dict[str, str] = {}
    found = attempt("qlty", errors, line_level, ctx, base_sha) + attempt(
        "betterleaks", errors, branch_secrets, settings, base_sha
    )
    if names:
        tools = [*COMPARED, *RATCHETS, *(["vulnerabilities"] if locks else [])]
        head = snapshot(settings, "HEAD", names, tools, full=True)
        base_snap = snapshot(settings, base_sha, names, tools, full=True)
        base_now = renamed(base_snap, renames)  # the base as if its renamed files already had their new paths
        added, removed = diff_lines(base_sha)
        removed = {renames.get(path, path): n for path, n in removed.items()}
        found += new_compared(head, base_now, set(changed), lines=added, removed=removed)
        found += ratchets(head, base_now, locks)
        base_partial = {f"base: {k}": v for k, v in partial_scans(base_snap).items()}
        errors |= head["errors"] | partial_scans(head) | base_errors(base_snap) | base_partial

    findings = sorted(
        ({**f, "new": True, "blocking": blocks(f, gate.block)} for f in found),
        key=lambda f: (not f["blocking"], f["severity"] != "error", f["tool"], f["file"], f.get("line") or 0),
    )
    mode = "warn" if report_only else gate.mode
    info = {"mode": mode, "head": head_short, "base": base_sha[:7], "changed": len(changed), "targets": names}
    write_jsonl(findings, out_dir / "gate-findings.jsonl")
    text = summary(findings, errors, info)
    (out_dir / "gate.md").write_text(text)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(step_summary).open("a") as fh:
            fh.write(text + "\n")
    failed = any(f["blocking"] for f in findings) or bool(errors)
    return GateOutcome(1 if mode == "block" and failed else 0, text, out_dir)
