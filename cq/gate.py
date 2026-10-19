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

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path

from cq import qlty
from cq.collectors import secrets
from cq.collectors.vale import EXTENSIONS
from cq.config import GateSettings, Settings, Target, ensure_data_dir
from cq.context import Context
from cq.findings import Finding, from_snapshot, make, mark_new, write_jsonl
from cq.proc import ToolError
from cq.runner import git, snapshot
from cq.toolconfig import gitleaks_config

COMPARED = ("aislop", "vale", "pyscn", "fallow", "semgrep", "docstrings")
# A symbol without a docstring counts when the branch adds it or changes a line in it; an untouched
# old one never does. Whole-file and coverage-% findings stay out of the gate.
SPAN_COUNTED = {("docstrings", "missing-docstring")}
RATCHETS = ("suppressions", "cognitive", "imports", "dependencies")
COUNT_RATCHETS = (("suppressions", "total"), ("imports", "violations"), ("dependencies", "issues"))
LOCKFILES = ("uv.lock", "package-lock.json")
VULN_GRADED = [("vulnerabilities", "critical"), ("vulnerabilities", "high")]
# Clones name a second file in their message: a clone of changed code counts even when its first
# file is the unchanged one.
CROSS_FILE = {"pyscn", "fallow"}
SHOWN = 50  # rows per table in the summary; the JSONL has them all
# qlty smells --upstream works per changed file, not per line: a smell counts only when its span
# overlaps a changed line. file-complexity spans the whole file, so any edit would hit it; growth
# in complexity is the cognitive ratchet's and check.sh's CC gate's job.
WHOLE_FILE_SMELLS = {"file-complexity"}
HUNK_SIDES = re.compile(r"^@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@")


def diff_status(base_sha: str) -> tuple[list[str], list[str], dict[str, str]]:
    """What the branch did since ``base_sha``: files on HEAD it added or changed, every path it
    touched (deletions and rename sources too), and its renames (old path → new path)."""
    changed: list[str] = []
    touched: list[str] = []
    renames: dict[str, str] = {}
    for line in git("diff", "--name-status", "-M", base_sha, "HEAD").splitlines():
        status, *paths = line.split("\t")
        touched += paths
        if status.startswith("R"):
            renames[paths[0]] = paths[1]
        if status[0] in "ACMR":
            changed.append(paths[-1])
    return changed, touched, renames


def diff_lines(base_sha: str) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """(the lines each file has on HEAD that the branch added or changed, the lines each file had on
    the base that the branch removed or changed), by HEAD path and base path."""
    added: dict[str, set[int]] = {}
    removed: dict[str, set[int]] = {}
    old_path = new_path = ""
    for line in git("diff", "--unified=0", "--no-color", base_sha, "HEAD").splitlines():
        if line.startswith("--- "):
            old_path = line[6:] if line.startswith("--- a/") else ""
        elif line.startswith("+++ "):
            new_path = line[6:] if line.startswith("+++ b/") else ""
        elif m := HUNK_SIDES.match(line):
            old_start, old_count = int(m["old"]), int(m["old_count"] or 1)
            new_start, new_count = int(m["new"]), int(m["new_count"] or 1)
            if new_path:
                added.setdefault(new_path, set()).update(range(new_start, new_start + new_count))
            if old_path:
                removed.setdefault(old_path, set()).update(range(old_start, old_start + old_count))
    return added, removed


def changed_lines(base_sha: str) -> dict[str, set[int]]:
    """The lines each file has on HEAD that the branch added or changed."""
    return diff_lines(base_sha)[0]


def overlaps(row: dict, lines: dict[str, set[int]]) -> bool:
    """Whether a finding's span covers a line the branch changed."""
    start = row.get("start_line") or row.get("line") or 0
    end = row.get("end_line") or start
    return any(n in lines.get(row["file"], set()) for n in range(start, end + 1))


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
        make("repo", "qlty", f"{r['plugin']}:{r['rule']}", r["severity"], r["msg"], r["file"], r["line"]) for r in check
    ]
    found += [
        make("repo", "smells", r["rule"], "warning", r["msg"], r["file"], r["line"])
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
        make(
            "repo",
            "secrets",
            f"in-branch-{r['rule']}",
            "error",
            f"{r['rule']} committed on this branch (commit {r['commit']}): drop it from the branch and rotate it",
            r["file"],
            r["line"],
        )
        for r in rows
    ]


def touches(f: Finding, changed: set[str]) -> bool:
    """Whether a compared finding is about a file the branch changed (a clone: either of its files)."""
    if f["file"] in changed:
        return True
    return f["tool"] in CROSS_FILE and any(re.search(rf"(^|\s){re.escape(c)}:", f["message"]) for c in changed)


def renamed(base: dict, renames: dict[str, str]) -> dict:
    """The base snapshot as if its renamed files already had their new paths, so a rename alone
    doesn't make the file's old findings look new."""
    if not renames:
        return base
    text = json.dumps(base)
    for old, new in renames.items():
        text = text.replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1])
    return json.loads(text)


def new_compared(
    head: dict,
    base: dict,
    changed: set[str],
    renames: dict[str, str] | None = None,
    lines: dict[str, set[int]] | None = None,
    removed: dict[str, set[int]] | None = None,
) -> list[Finding]:
    """Compared tools' findings whose key the base lacks, in changed files.

    An app the base doesn't have yet has nothing to compare with, so its findings all count as new.
    A tool that failed on the base is not compared at all: run_gate lists it as a base error.
    A symbol missing its docstring also counts when the branch changed a line inside it: a line it
    has on HEAD (``lines``), or one it had on the base and lost (``removed``, by base path, judged
    against the symbol's span on the base).
    """
    base_now = renamed(base, renames or {})
    marked = mark_new(from_snapshot(head), base_now)
    on_base = {f["key"]: f for f in from_snapshot(base_now)}
    removed_now = {(renames or {}).get(path, path): n for path, n in (removed or {}).items()}
    return [f for f in marked if counted(f, base, changed, lines or {}, on_base, removed_now)]


def counted(
    f: Finding,
    base: dict,
    changed: set[str],
    lines: dict[str, set[int]],
    on_base: dict[str, Finding] | None = None,
    removed: dict[str, set[int]] | None = None,
) -> bool:
    """Whether one compared finding is the branch's: new, or (docstrings) in a symbol it changed."""
    if f["tool"] not in COMPARED or not touches(f, changed):
        return False
    if (f["tool"], f["rule"]) == ("docstrings", "missing-docstrings"):
        return False  # the per-file count: the gate judges symbols, not coverage
    if is_new(f, base):
        return True
    # A module's span is the whole file: an old module gap counts only through its key (a new file).
    if (f["tool"], f["rule"]) not in SPAN_COUNTED or f["symbol"] == "(module)":
        return False
    before = (on_base or {}).get(f["key"])
    return overlaps(f, lines) or (before is not None and overlaps(before, removed or {}))


def is_new(f: Finding, base: dict) -> bool:
    """New against the base: a key it lacks, or an app it doesn't have (``new`` None for both kinds of
    "didn't run"; only the missing app counts, a tool that failed on the base is a base error)."""
    if f["new"] is None:
        return f["target"] not in base["targets"]
    return bool(f["new"])


def partial_scans(head: dict) -> dict[str, str]:
    """Semgrep runs that skipped files or rules (its nonfatal errors): a partial scan is a blind spot."""
    out = {}
    for target in head["targets"]:
        skipped = value(head, target, "semgrep", "scan_errors")
        if skipped:
            out[f"{target}.semgrep"] = f"{skipped:g} files or rules could not be scanned (see semgrep.log)"
    return out


def base_errors(base: dict) -> dict[str, str]:
    """The base's tool errors, apart from apps the base doesn't have yet: those findings were not compared."""
    return {f"base: {k}": v for k, v in base["errors"].items() if v != "not present at this commit"}


def measured(snap: dict, target: str, tool: str, field: str) -> object:
    """One field of a tool's result in a snapshot, or None when the tool didn't measure it."""
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- a target or tool the snapshot didn't measure is None, by contract
    data = snap["targets"].get(target, {}).get(tool)
    return data.get(field) if isinstance(data, dict) else None


def value(snap: dict, target: str, tool: str, field: str) -> float | None:
    """One number from a snapshot, or None when the tool didn't measure it."""
    found = measured(snap, target, tool, field)
    return found if isinstance(found, int | float) else None


def cognitive_ratchet(target: str, head: dict, base: dict) -> list[Finding]:
    """Functions newly over the cognitive limit, or over it and grown."""
    after = measured(head, target, "cognitive", "over_limit_items")
    before = {} if target not in base["targets"] else measured(base, target, "cognitive", "over_limit_items")
    if not isinstance(after, dict) or not isinstance(before, dict):
        return []
    out = []
    for function, score in after.items():
        was = before.get(function)
        if was is None or score > was:
            change = "newly over the limit" if was is None else f"grew from {was}"
            out.append(
                make(
                    target,
                    "ratchet",
                    "cognitive",
                    "warning",
                    f"{function}: cognitive {score}, {change}",
                    function.split("::")[0],
                    symbol=function,
                )
            )
    return out


def ratchets(head: dict, base: dict, locks: set[str]) -> list[Finding]:
    """Every measure that may not get worse than the base."""
    out: list[Finding] = []
    for target in head["targets"]:
        checks = list(COUNT_RATCHETS)
        if target in locks:
            # npm audit grades by severity; pip-audit doesn't, so for Python every advisory counts.
            graded = measured(head, target, "vulnerabilities", "critical") is not None
            checks += VULN_GRADED if graded else [("vulnerabilities", "vulnerabilities")]
        # An app the base doesn't have starts from zero; a tool that failed on the base is skipped
        # (it is listed as a base error).
        new_app = target not in base["targets"]
        for tool, field in checks:
            after = value(head, target, tool, field)
            before = 0 if new_app else value(base, target, tool, field)
            if after is not None and before is not None and after > before:
                message = f"{tool}.{field} went from {before:g} to {after:g}"
                out.append(make(target, "ratchet", f"{tool}.{field}", "warning", message))
        out += cognitive_ratchet(target, head, base)
    return out


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
