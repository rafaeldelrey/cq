"""The gate's comparison of a branch with its base: new findings from the compared tools, and the ratchets."""

import json
import re

from cq.diff import overlaps
from cq.finding import Finding, Source
from cq.findings import from_snapshot, mark_new
from cq.symbols import Span, covered

COMPARED = ("aislop", "vale", "pyscn", "fallow", "semgrep", "docstrings")
# A symbol without a docstring counts when the branch adds it or changes a line in it; an untouched
# old one never does. Whole-file and coverage-% findings stay out of the gate.
SPAN_COUNTED = {("docstrings", "missing-docstring")}
COUNT_RATCHETS = (("suppressions", "total"), ("imports", "violations"), ("dependencies", "issues"))
VULN_GRADED = [("vulnerabilities", "critical"), ("vulnerabilities", "high")]
# Clones name a second file in their message: a clone of changed code counts even when its first
# file is the unchanged one.
CROSS_FILE = {"pyscn", "fallow"}
# Tools that read only the Python syntax tree: a finding of theirs whose code sits wholly in symbols
# the branch left unchanged (docstrings and comments aside) existed on the base, whatever its key says.
# A docstring added at a clone's first line changes that line's fingerprint, so the key alone calls it new.
TREE_ONLY = {"pyscn"}
# pyscn's message names both copies: "8 lines 90% similar: <file>:<line> and <file>:<line>".
COPIES = re.compile(r"similar: (.+):(\d+) and (.+):(\d+)$")


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
    unchanged: dict[str, list[Span]] | None = None,
) -> list[Finding]:
    """Compared tools' findings whose key the base lacks, in changed files.

    An app the base doesn't have yet has nothing to compare with, so its findings all count as new.
    A tool that failed on the base is not compared at all: run_gate lists it as a base error.
    A symbol missing its docstring also counts when the branch changed a line inside it: a line it
    has on HEAD (``lines``), or one it had on the base and lost (``removed``, by base path, judged
    against the symbol's span on the base). A tree-only finding wholly in ``unchanged`` code is old.
    """
    base_now = renamed(base, renames or {})
    marked = mark_new(from_snapshot(head), base_now)
    on_base = {f["key"]: f for f in from_snapshot(base_now)}
    removed_now = {(renames or {}).get(path, path): n for path, n in (removed or {}).items()}
    return [
        f
        for f in marked
        if counted(f, base, changed, lines or {}, on_base, removed_now)
        and not in_unchanged_code(f, changed, unchanged or {})
    ]


def in_unchanged_code(f: Finding, changed: set[str], unchanged: dict[str, list[Span]]) -> bool:
    """Whether a tree-only finding's copies all sit, every line of them, in code the branch left as
    it was: in a file it didn't change, or inside one symbol whose tree is the same on both sides.
    A finding whose copies' extent isn't known (no lengths) is never set aside."""
    m = COPIES.search(f["message"]) if f["tool"] in TREE_ONLY else None
    lengths = f.get("copy_lines")
    if not m or not isinstance(lengths, list) or len(lengths) != 2 or not all(isinstance(n, int) for n in lengths):
        return False
    copies = [(m[1], int(m[2]), lengths[0]), (m[3], int(m[4]), lengths[1])]
    return all(
        path not in changed or covered(start, start + n - 1, unchanged.get(path, [])) for path, start, n in copies
    )


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
                Source(target, "ratchet").finding(
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
                out.append(Source(target, "ratchet").finding(f"{tool}.{field}", "warning", message))
        out += cognitive_ratchet(target, head, base)
    return out
