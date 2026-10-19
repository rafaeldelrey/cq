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
