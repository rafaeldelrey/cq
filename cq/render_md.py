"""The trend report as Markdown: for the terminal, and for Claude to read and summarise."""

from cq import findings
from cq.metrics import Metric, Row, rows_for, verdict

SPARK = "▁▂▃▄▅▆▇█"
MARK = {"better": "✅", "worse": "🔻", "same": "·", "": ""}


def fmt(value: float | None, metric: Metric) -> str:
    """A value with its unit; integers without decimals."""
    if value is None:
        return "—"
    text = f"{value:,.0f}" if value == int(value) and not metric.unit else f"{value:,.2f}"
    return f"{text}{metric.unit}"


def fmt_delta(row: Row, other: float | None) -> str:
    """Signed change with its better/worse mark."""
    delta = row.delta(other)
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    text = f"{sign}{delta:,.0f}" if delta == int(delta) else f"{sign}{delta:,.2f}"
    return f"{text} {MARK[verdict(row.metric, delta)]}".strip()


def sparkline(values: list[float]) -> str:
    """Unicode sparkline of up to the last 24 points."""
    values = values[-24:]
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK[3] * len(values)
    return "".join(SPARK[round((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in values)


def changes(rows: list[Row]) -> tuple[list[str], list[str]]:
    """Lines for the metrics that got better / worse since the previous snapshot."""
    better, worse = [], []
    for r in rows:
        v = verdict(r.metric, r.delta(r.prev))
        line = f"**{r.target}** {r.metric.label}: {fmt(r.prev, r.metric)} → {fmt(r.now, r.metric)}"
        if v == "better":
            better.append(line)
        elif v == "worse":
            worse.append(line)
    return better, worse


def hot_spots(target: dict) -> list[str]:
    """The worst offenders of the latest snapshot, a few per tool."""
    out: list[str] = []

    def section(title: str, lines: list[str]) -> None:
        if lines:
            out.append(f"**{title}**")
            out.extend(f"- {line}" for line in lines)
            out.append("")

    cx = target.get("complexity", {})
    section(
        f"Highest cyclomatic complexity (limit {cx.get('limit')})",
        [f"`{w['function']}` CC {w['cc']} ({w['rank']})" for w in cx.get("worst", [])[:8]],
    )
    cog = target.get("cognitive", {})
    section("Highest cognitive complexity", [f"`{w['function']}` {w['score']}" for w in cog.get("worst", [])[:8]])
    mi = target.get("maintainability", {})
    section(
        "Lowest maintainability", [f"`{w['file']}` MI {w['mi']} ({w['lines']} lines)" for w in mi.get("worst", [])[:5]]
    )
    cov = target.get("tests", {}).get("coverage", {})
    section(
        "Least-covered files",
        [f"`{w['file']}` {w['percent']}% ({w['missing']} lines missing)" for w in cov.get("worst_files", [])[:8]],
    )
    dead = target.get("dead_code", {})
    section(
        "Dead code, high confidence",
        [
            f"`{i['file']}:{i['line']}` {i['msg']} ({i['confidence']}%)"
            for i in dead.get("items", [])
            if i["confidence"] >= 90
        ],
    )
    dup = target.get("duplication", {})
    section("Largest clones", [f"{c['lines']} lines: `{c['a']}` ≈ `{c['b']}`" for c in dup.get("top", [])[:6]])
    doc = target.get("docstrings", {})
    section(
        "Most missing docstrings", [f"`{f['file']}` {f['missing']} missing" for f in doc.get("worst_files", [])[:6]]
    )
    for tool in ("ruff", "pyright", "eslint", "typecheck"):
        items = target.get(tool, {}).get("items", [])
        section(f"{tool} findings", [f"`{i['file']}:{i['line']}` {i.get('code') or ''} {i['msg']}" for i in items[:10]])
    return out


NEW_TOOLS = ("semgrep", "imports", "dependencies", "vulnerabilities", "secrets", "mutation")
SEVERITY_MARK = {"error": "⛔", "warning": "⚠️", "info": "·"}


def finding_line(f: dict) -> str:
    """One finding as a Markdown list item."""
    where = f"`{f['file']}{':' + str(f['line']) if f.get('line') else ''}`" if f["file"] else ""
    symbol = f" `{f['symbol']}`" if f.get("symbol") and f["tool"] in ("complexity", "cognitive") else ""
    return f"- {SEVERITY_MARK[f['severity']]} **{f['tool']}/{f['rule']}** {where}{symbol} {f['message']}".rstrip()


def new_findings_section(found: list[dict]) -> list[str]:
    """The findings this snapshot has and the previous one did not, errors and warnings first."""
    fresh = [f for f in found if f.get("new")]
    if not fresh:
        return []
    shown = fresh[:15]
    more = f" (showing {len(shown)} of {len(fresh)})" if len(fresh) > len(shown) else ""
    return [f"**New since the previous snapshot{more}**", *(finding_line(f) for f in shown), ""]


def tool_sections(found: list[dict]) -> list[str]:
    """The newer tools' findings for one target, a few per tool, most severe first."""
    out: list[str] = []
    for tool in NEW_TOOLS:
        mine = [f for f in found if f["tool"] == tool]
        if mine:
            counts = ", ".join(
                f"{sum(1 for f in mine if f['severity'] == s)} {s}"
                for s in ("error", "warning", "info")
                if any(f["severity"] == s for f in mine)
            )
            out += [f"**{tool}** ({counts})", *(finding_line(f) for f in mine[:8]), ""]
    return out


def measurement_changes(previous: dict, latest: dict) -> list[str]:
    """What changed in how the two snapshots were measured: tool versions and the configs read.

    A metric that steps between them may be the measurement, not the code. A snapshot from before
    cq recorded its configs says nothing about them, so only versions are compared then.
    """
    notes = []
    old_tools, new_tools = previous.get("tool_versions", {}), latest.get("tool_versions", {})
    for tool in sorted(set(old_tools) | set(new_tools)):
        if old_tools.get(tool) != new_tools.get(tool):
            notes.append(f"{tool}: {old_tools.get(tool, 'none')} → {new_tools.get(tool, 'none')}")
    if "configs" in previous:
        old, new = previous["configs"], latest.get("configs", {})
        for name in sorted(set(old) | set(new)):
            before, after = old.get(name), new.get(name)
            if before is None or after is None or before["hash"] != after["hash"]:
                source = (after or before or {}).get("file", "")
                verb = "added" if before is None else "removed" if after is None else "changed"
                notes.append(f"{name} config {verb} ({source})")
    return notes


def step_section(notes: list[str]) -> list[str]:
    """The report section that flags a measurement change, or nothing."""
    if not notes:
        return []
    return [
        "## Measurement changed since the previous snapshot",
        "",
        "A step in a metric below may come from these, not from the code:",
        "",
        *(f"- {n}" for n in notes),
        "",
    ]


def render(history: list[dict], window_days: int = 30, details: bool = True) -> str:
    """The whole report."""
    latest = history[-1]
    all_findings = findings.mark_new(findings.from_snapshot(latest), history[-2] if len(history) > 1 else None)
    lines = [
        f"# Code quality: {latest['commit_date'][:10]} ({latest['short']}{', dirty tree' if latest['dirty'] else ''})",
        "",
        f"{len(history)} snapshot(s), {history[0]['commit_date'][:10]} → {latest['commit_date'][:10]}. "
        f"Δ prev = since the previous snapshot; Δ {window_days}d = since the last snapshot "
        f"{window_days} days before this one; Δ first = since the first.",
        "",
    ]
    all_rows = {name: rows_for(history, name, window_days) for name in latest["targets"]}
    better, worse = changes([r for rows in all_rows.values() for r in rows])
    if len(history) > 1:
        lines += ["## Since the previous snapshot", ""]
        lines += [f"- 🔻 {w}" for w in worse] or ["- No metric got worse."]
        lines += [f"- ✅ {b}" for b in better]
        lines.append("")
    if latest["errors"]:
        lines += ["## Tool errors", ""] + [f"- `{k}`: {v.splitlines()[0][:200]}" for k, v in latest["errors"].items()]
        lines.append("")
    for name, rows in all_rows.items():
        lines += [
            f"## {name}",
            "",
            f"| Metric | Now | Δ prev | Δ {window_days}d | Δ first | Trend |",
            "|---|--:|--:|--:|--:|---|",
        ]
        for r in rows:
            spark = sparkline([v for _, v in r.points])
            lines.append(
                f"| {r.metric.label} | {fmt(r.now, r.metric)} | {fmt_delta(r, r.prev)} | "
                f"{fmt_delta(r, r.window)} | {fmt_delta(r, r.first)} | {spark} |"
            )
        lines.append("")
        mine = [f for f in all_findings if f["target"] == name]
        lines += new_findings_section(mine)
        if details:
            lines += hot_spots(latest["targets"][name])
            lines += tool_sections(mine)
    return "\n".join(lines).rstrip() + "\n"
