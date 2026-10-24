"""The trend report as Markdown: for the terminal, and for Claude to read and summarise."""

from cq import benchmarks, findings
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


def titled_list(title: str, lines: list[str]) -> list[str]:
    """A bold title over a bullet list, or nothing when the list is empty."""
    return [f"**{title}**", *(f"- {line}" for line in lines), ""] if lines else []


def tool_result(target: dict, tool: str) -> dict:
    """A tool's result in one target; empty when the tool didn't run (its error is listed in the report)."""
    return target.get(tool, {})


def tool_list(target: dict, tool: str, key: str) -> list[dict]:
    """One list from a tool's result; empty when the tool didn't run or listed nothing."""
    return tool_result(target, tool).get(key, [])


def complexity_spots(target: dict) -> list[tuple[str, list[str]]]:
    """The most complex functions and the least maintainable files."""
    limit = tool_result(target, "complexity").get("limit")
    return [
        (
            f"Highest cyclomatic complexity (limit {limit})",
            [f"`{w['function']}` CC {w['cc']} ({w['rank']})" for w in tool_list(target, "complexity", "worst")[:8]],
        ),
        (
            "Highest cognitive complexity",
            [f"`{w['function']}` {w['score']}" for w in tool_list(target, "cognitive", "worst")[:8]],
        ),
        (
            "Lowest maintainability",
            [
                f"`{w['file']}` MI {w['mi']} ({w['lines']} lines)"
                for w in tool_list(target, "maintainability", "worst")[:5]
            ],
        ),
    ]


def hygiene_spots(target: dict) -> list[tuple[str, list[str]]]:
    """The least-covered files, high-confidence dead code, the largest clones and the missing docstrings."""
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- a tool that didn't run has no section; its error is listed in the report
    coverage = tool_result(target, "tests").get("coverage", {}).get("worst_files", [])
    dead = [i for i in tool_list(target, "dead_code", "items") if i["confidence"] >= 90]
    return [
        (
            "Least-covered files",
            [f"`{w['file']}` {w['percent']}% ({w['missing']} lines missing)" for w in coverage[:8]],
        ),
        ("Dead code, high confidence", [f"`{i['file']}:{i['line']}` {i['msg']} ({i['confidence']}%)" for i in dead]),
        (
            "Largest clones",
            [f"{c['lines']} lines: `{c['a']}` ≈ `{c['b']}`" for c in tool_list(target, "duplication", "top")[:6]],
        ),
        (
            "Most missing docstrings",
            [f"`{f['file']}` {f['missing']} missing" for f in tool_list(target, "docstrings", "worst_files")[:6]],
        ),
    ]


def checker_spots(target: dict) -> list[tuple[str, list[str]]]:
    """The first findings of the linters and type checkers."""
    return [
        (
            f"{tool} findings",
            [
                f"`{i['file']}:{i['line']}` {i.get('code') or ''} {i['msg']}"
                for i in tool_list(target, tool, "items")[:10]
            ],
        )
        for tool in ("ruff", "pyright", "eslint", "typecheck")
    ]


def hot_spots(target: dict) -> list[str]:
    """The worst offenders of the latest snapshot, a few per tool."""
    sections = [*complexity_spots(target), *hygiene_spots(target), *checker_spots(target)]
    return [line for title, lines in sections for line in titled_list(title, lines)]


NEW_TOOLS = ("semgrep", "imports", "dependencies", "vulnerabilities", "secrets", "mutation")
SEVERITY_MARK = {"error": "⛔", "warning": "⚠️", "info": "·"}


def finding_line(f: dict) -> str:
    """One finding as a Markdown list item."""
    if not f["file"]:
        where = ""
    elif f.get("line"):
        where = f"`{f['file']}:{f['line']}`"
    else:
        where = f"`{f['file']}`"
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


def change_verb(before: dict | None, after: dict | None) -> str:
    """How a config moved between two snapshots: added, removed or changed."""
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "changed"


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
                notes.append(f"{name} config {change_verb(before, after)} ({source})")
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


def since_previous(rows: list[Row]) -> list[str]:
    """What got worse and what got better since the previous snapshot."""
    better, worse = changes(rows)
    lines = ["## Since the previous snapshot", ""]
    lines += [f"- 🔻 {w}" for w in worse] or ["- No metric got worse."]
    return [*lines, *(f"- ✅ {b}" for b in better), ""]


def errors_section(errors: dict[str, str]) -> list[str]:
    """The tools that didn't run, each with the first line of its error."""
    if not errors:
        return []
    return ["## Tool errors", "", *(f"- `{k}`: {v.splitlines()[0][:200]}" for k, v in errors.items()), ""]


def grade_line(target: dict) -> list[str]:
    """The target's overall grade against the published benchmarks, when it has one."""
    found = benchmarks.grade(target)
    if found is None:
        return []
    return [f"{found.text()}. Ratings: published benchmarks, see `{benchmarks.REFERENCE}`.", ""]


def rating_cell(row: Row) -> str:
    """The metric's rating at its current value, or blank where no published scale applies."""
    rating = benchmarks.rate(row.metric, row.now)
    return rating.text() if rating else ""


def metrics_table(name: str, rows: list[Row], window_days: int, target: dict) -> list[str]:
    """One target's metrics: now, its rating, the three deltas and the trend, under the target's grade."""
    lines = [
        f"## {name}",
        "",
        *grade_line(target),
        f"| Metric | Now | Rating | Δ prev | Δ {window_days}d | Δ first | Trend |",
        "|---|--:|---|--:|--:|--:|---|",
    ]
    for r in rows:
        spark = sparkline([v for _, v in r.points])
        lines.append(
            f"| {r.metric.label} | {fmt(r.now, r.metric)} | {rating_cell(r)} | {fmt_delta(r, r.prev)} | "
            f"{fmt_delta(r, r.window)} | {fmt_delta(r, r.first)} | {spark} |"
        )
    return [*lines, ""]


def cut_section(cuts: list[dict]) -> list[str]:
    """The lists a target's tools kept short, each as "kept of total": counting their rows stops short."""
    if not cuts:
        return []
    parts = [f"`{c['tool']}` {c['list']} {c['shown']} of {c['total']}" for c in cuts]
    return [f"**Lists kept short** (count from the snapshot's totals, not the rows): {'; '.join(parts)}", ""]


def header(history: list[dict], window_days: int) -> list[str]:
    """The report's title (the measured commit) and what its delta columns mean."""
    latest = history[-1]
    dirty = ", dirty tree" if latest["dirty"] else ""
    return [
        f"# Code quality: {latest['commit_date'][:10]} ({latest['short']}{dirty})",
        "",
        f"{len(history)} snapshot(s), {history[0]['commit_date'][:10]} → {latest['commit_date'][:10]}. "
        f"Δ prev = since the previous snapshot; Δ {window_days}d = since the last snapshot "
        f"{window_days} days before this one; Δ first = since the first.",
        "",
    ]


def target_section(
    latest: dict, name: str, rows: list[Row], found: list[dict], window_days: int, *, details: bool
) -> list[str]:
    """One target's part of the report: its metrics, its cut lists, its new findings and, with details,
    its worst offenders and the newer tools' findings."""
    mine = [f for f in found if f["target"] == name]
    lines = metrics_table(name, rows, window_days, latest["targets"][name])
    lines += cut_section([c for c in findings.cut_lists(latest) if c["target"] == name])
    lines += new_findings_section(mine)
    if details:
        lines += hot_spots(latest["targets"][name]) + tool_sections(mine)
    return lines


def render(history: list[dict], window_days: int = 30, details: bool = True) -> str:
    """The whole report."""
    latest = history[-1]
    all_findings = findings.mark_new(findings.from_snapshot(latest), history[-2] if len(history) > 1 else None)
    lines = header(history, window_days)
    all_rows = {name: rows_for(history, name, window_days) for name in latest["targets"]}
    if len(history) > 1:
        lines += since_previous([r for rows in all_rows.values() for r in rows])
    lines += errors_section(latest["errors"])
    for name, rows in all_rows.items():
        lines += target_section(latest, name, rows, all_findings, window_days, details=details)
    return "\n".join(lines).rstrip() + "\n"
