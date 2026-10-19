"""Every tool's findings in a snapshot, in one list: the work list an agent can act on.

A run writes them to runs/<run>/findings.jsonl, most severe first, each flagged ``new`` when the
previous snapshot didn't have its key. The shape of one finding is in ``cq.finding``; how each tool's
result becomes findings, in ``cq.extractors``.
"""

import json
from pathlib import Path

from cq.extractors import EXTRACTORS
from cq.finding import Finding, Source

SEVERITIES = ("error", "warning", "info")


def extract(target: str, tool: str, data: dict) -> list[Finding]:
    """One tool's findings in one target, from its result in a snapshot."""
    return list(EXTRACTORS[tool](Source(target, tool), data))


def from_snapshot(snap: dict) -> list[Finding]:
    """Every finding in a snapshot, most severe first."""
    out: list[Finding] = []
    for target, tools in snap["targets"].items():
        for tool, data in tools.items():
            if tool in EXTRACTORS and isinstance(data, dict) and "skipped" not in data:
                out.extend(extract(target, tool, data))
    for name, error in snap.get("errors", {}).items():
        target, _, tool = name.partition(".")
        message = f"the tool did not run: {error.splitlines()[0][:200]}"
        out.append(Source(target, tool or "run").finding("tool-failed", "error", message))
    return sorted(out, key=lambda f: (SEVERITIES.index(f["severity"]), f["target"], f["tool"], f["file"]))


def listed(result: dict, name: str) -> int:
    """How many entries a result's list (dotted name for a nested one) kept after its cut."""
    node: object = result
    for key in name.split("."):
        node = node.get(key, []) if isinstance(node, dict) else []
    return len(node) if isinstance(node, list | dict) else 0


def cut_lists(snap: dict) -> list[dict]:
    """Every list a snapshot's collectors cut to fit: target, tool, list, how many it kept, and the true total."""
    return [
        {"target": target, "tool": tool, "list": name, "shown": listed(result, name), "total": total}
        for target, tools in snap["targets"].items()
        for tool, result in tools.items()
        if isinstance(result, dict)
        for name, total in result.get("truncated", {}).items()
    ]


def totals(snap: dict, found: list[Finding]) -> dict:
    """findings-totals.json: per target and tool, the findings findings.jsonl holds, and each cut list's
    true total, so a reader counting rows knows where they stop short."""
    out: dict[str, dict[str, dict]] = {}
    for f in found:
        entry = out.setdefault(f["target"], {}).setdefault(f["tool"], {"findings": 0, "cut": {}})
        entry["findings"] += 1
    for c in cut_lists(snap):
        entry = out.setdefault(c["target"], {}).setdefault(c["tool"], {"findings": 0, "cut": {}})
        entry["cut"][c["list"]] = {"shown": c["shown"], "total": c["total"]}
    return {"targets": out}


def write_totals(snap: dict, found: list[Finding], path: Path) -> None:
    """Write findings-totals.json."""
    path.write_text(json.dumps(totals(snap, found), indent=1) + "\n")


def mark_new(findings: list[Finding], previous: dict | None) -> list[Finding]:
    """Flag the findings whose key the previous snapshot did not have.

    A tool the previous snapshot didn't run (new tool, old commit) has nothing to compare with,
    so its findings are not called new.
    """
    if previous is None:
        return [{**f, "new": None} for f in findings]
    before = {f["key"] for f in from_snapshot(previous)}
    ran_before = {(t, tool) for t, tools in previous["targets"].items() for tool in tools}
    return [
        {**f, "new": (f["key"] not in before) if (f["target"], f["tool"]) in ran_before else None} for f in findings
    ]


def write_jsonl(findings: list[Finding], path: Path) -> None:
    """One finding per line."""
    path.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in findings))
