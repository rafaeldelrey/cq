"""The interactive HTML dashboard of one run: the template in dashboard.html, filled with JSON.

Everything the page shows is embedded, so the file works opened from disk and published as an
Artifact alike: the history's headline metrics (for the trends), the run's own snapshot (for
the findings tables), the files in the run folder, and the written analysis when there is one.
"""

import html
import json
import re
from datetime import datetime
from pathlib import Path

from cq import benchmarks
from cq.metrics import METRICS
from cq.render_md import measurement_changes

TEMPLATE = Path(__file__).with_name("dashboard.html")
STATIC_MARKER = "<!--__STATIC__-->"  # where the static report goes, in <main>
DATA_MARKER = "/*__DATA__*/{}"  # where the page's JSON goes
TABLE_ROW = re.compile(r"^\s*\|")
ALIGNMENT = re.compile(r"^\s*:?-+:?\s*$")  # a table's second row: |---|--:|
PIPE_END = re.compile(r"(?<!\\)\|$")  # the row's closing border, not an escaped pipe in its last cell
PIPE = re.compile(r"(?<!\\)\|")  # a cell border; "\|" is a pipe inside a cell
HEADING = re.compile(r"^(#{1,4})\s+(.*)")  # the analysis' Markdown: headings and list items
LIST_ITEM = re.compile(r"^\s*([-*]|\d+\.)\s+(.*)")


def history_until(history: list[dict], snap: dict) -> list[dict]:
    """The history as it stood at this run: earlier commits and this commit's earlier runs, then the run itself.

    Every complete run keeps its own entry (#54), so a re-run's report compares it with the run
    before it at the same commit.
    """
    cutoff = datetime.fromisoformat(snap["commit_date"])
    earlier = [s for s in history if s["run_id"] != snap["run_id"] and before(s, snap, cutoff)]
    return [*earlier, snap]


def before(other: dict, snap: dict, cutoff: datetime) -> bool:
    """Whether ``other`` came before ``snap`` in the trend: an earlier commit, or an earlier run of its commit."""
    if other["commit"] == snap["commit"]:
        return other["run_at"] < snap["run_at"]
    return datetime.fromisoformat(other["commit_date"]) <= cutoff


def series_point(snap: dict) -> dict:
    """One snapshot reduced to the headline metrics of each target."""
    values = {
        name: {m.path: v for m in METRICS if (v := m.read(target)) is not None}
        for name, target in snap["targets"].items()
    }
    return {
        "short": snap["short"],
        "date": snap["commit_date"],
        "run_at": snap["run_at"],
        "subject": snap["subject"],
        "dirty": snap["dirty"],
        "values": values,
    }


def ratings(snap: dict) -> dict[str, dict]:
    """Each target's rated metrics and overall grade at this run, against the published benchmarks."""
    out = {}
    for name, target in snap["targets"].items():
        rated = {m.path: r for m in METRICS if (r := benchmarks.rate(m, m.read(target))) is not None}
        found = benchmarks.grade(target)
        out[name] = {
            "metrics": {path: {"label": r.label, "band": r.band} for path, r in rated.items()},
            "grade": None
            if found is None
            else {"label": found.label, "rated": found.rated, "weakest": found.weakest.label},
        }
    return out


def run_files(run_dir: Path) -> list[dict]:
    """What the run folder holds, for the "Raw output" list."""
    files = []
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and p.name not in {"dashboard.html", ".coverage"} and "jscpd" not in p.parent.name:
            files.append({"path": p.relative_to(run_dir).as_posix(), "bytes": p.stat().st_size})
    return files[:400]


INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"), r'<a href="\2" target="_blank" rel="noopener">\1</a>'),
]


def inline(text: str) -> str:
    """Escape, then the inline Markdown the analysis uses: code, bold, links."""
    out = html.escape(text)  # quotes too: a link's URL lands in an href attribute
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    return out


def table_cells(row: list[str], tag: str, right: list[bool]) -> str:
    """One table row's cells as ``tag`` elements; a column the alignment row marks ``--:`` is right-aligned."""
    return "".join(
        f"<{tag}{' class=num' if i < len(right) and right[i] else ''}>{inline(cell)}</{tag}>"
        for i, cell in enumerate(row)
    )


class HtmlBuilder:
    """The HTML for markdown_to_html, built one Markdown line at a time."""

    def __init__(self) -> None:
        """An empty document: no open paragraph, no open list."""
        self.out: list[str] = []
        self.list_kind: str | None = None
        self.para: list[str] = []
        self.rows: list[list[str]] = []  # the open table's rows, cells as written

    def end_paragraph(self) -> None:
        """Close the open paragraph, if any."""
        if self.para:
            self.out.append(f"<p>{inline(' '.join(self.para))}</p>")
            self.para.clear()

    def end_list(self) -> None:
        """Close the open list, if any."""
        if self.list_kind:
            self.out.append(f"</{self.list_kind}>")
            self.list_kind = None

    def end_table(self) -> None:
        """Close the open table, if any: its first row is the header, and an alignment row under it
        right-aligns the columns written ``--:``."""
        if not self.rows:
            return
        head, *body = self.rows
        right = [cell.endswith(":") for cell in body[0]] if body and all(ALIGNMENT.match(c) for c in body[0]) else []
        body = body[1:] if right else body
        rows = "".join(f"<tr>{table_cells(r, 'td', right)}</tr>" for r in body)
        self.out.append(
            f'<div class="table-wrap"><table><thead><tr>{table_cells(head, "th", right)}</tr></thead><tbody>{rows}</tbody></table></div>'
        )
        self.rows = []

    def row(self, line: str) -> None:
        """A table row: ``| a | b |``; a pipe written ``\\|`` stays inside its cell."""
        self.end_paragraph()
        self.end_list()
        inner = PIPE_END.sub("", line.strip().removeprefix("|"))
        self.rows.append([cell.strip().replace("\\|", "|") for cell in PIPE.split(inner)])

    def blank(self) -> None:
        """A blank line ends the paragraph, the list and the table."""
        self.end_paragraph()
        self.end_list()
        self.end_table()

    def heading(self, m: re.Match[str]) -> None:
        """``#`` to ``####`` become h3 to h6: the dashboard's own headings come first."""
        self.blank()
        level = min(len(m[1]) + 2, 6)
        self.out.append(f"<h{level}>{inline(m[2])}</h{level}>")

    def item(self, m: re.Match[str]) -> None:
        """A list item, opening its list (numbered or not) when the kind changes."""
        self.end_paragraph()
        self.end_table()
        kind = "ol" if m[1][0].isdigit() else "ul"
        if self.list_kind != kind:
            self.end_list()
            self.out.append(f"<{kind}>")
            self.list_kind = kind
        self.out.append(f"<li>{inline(m[2])}</li>")

    def document(self) -> str:
        """The HTML so far, with any open paragraph or list closed."""
        self.blank()
        return "\n".join(self.out)

    def text(self, line: str) -> None:
        """A line of a paragraph: it ends any list or table."""
        self.end_list()
        self.end_table()
        self.para.append(line.strip())


def markdown_to_html(md: str) -> str:
    """The small Markdown subset the analysis and report.md are written in: headings, lists,
    paragraphs and pipe tables, with inline code, bold and links."""
    html = HtmlBuilder()
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            html.blank()
        elif TABLE_ROW.match(line):
            html.row(line)
        elif heading := HEADING.match(line):
            html.heading(heading)
        elif item := LIST_ITEM.match(line):
            html.item(item)
        else:
            html.text(line)
    return html.document()


def static_report(analysis_md: str | None, report_md: str | None) -> str:
    """The analysis and the report as plain HTML: the page when its script doesn't run. Nothing
    without a report (a caller from before it was passed)."""
    if not report_md:
        return ""
    parts = [
        '<section class="panel" id="static-report">',
        '<p class="sub">The interactive dashboard needs JavaScript. This is the run\'s report.</p>',
        markdown_to_html(analysis_md) if analysis_md else "",
        markdown_to_html(report_md),
        "</section>",
    ]
    return "\n".join(p for p in parts if p)


def build(
    history: list[dict],
    snap: dict,
    run_dir: Path,
    analysis_md: str | None = None,
    found: list[dict] | None = None,
    title: str = "Code quality",
    report_md: str | None = None,
) -> str:
    """The dashboard page for the run whose snapshot is ``snap``, headed ``title``.

    The page also carries the analysis and ``report_md`` as plain HTML, shown wherever its script
    doesn't run (a file preview, a mail attachment) and hidden by the script.
    """
    upto = history_until(history, snap)
    points = [series_point(s) for s in upto]
    data = {
        "metrics": [
            {"path": m.path, "label": m.label, "better": m.better, "unit": m.unit, "group": m.group} for m in METRICS
        ],
        "series": points,
        "ratings": ratings(snap),
        "reference": benchmarks.REFERENCE,
        "snapshot": snap,
        "files": run_files(run_dir),
        "analysis": markdown_to_html(analysis_md) if analysis_md else None,
        "findings": found or [],
        "steps": measurement_changes(upto[-2], snap) if len(upto) > 1 else [],
    }
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    # Split at the markers rather than replace, so nothing in the report or the data reads as one.
    page = TEMPLATE.read_text().replace("__TITLE__", html.escape(title))
    if STATIC_MARKER not in page or DATA_MARKER not in page:
        raise ValueError(f"{TEMPLATE} has lost its {STATIC_MARKER} or {DATA_MARKER} marker")
    top, _, rest = page.partition(STATIC_MARKER)
    middle, _, end = rest.partition(DATA_MARKER)
    return top + static_report(analysis_md, report_md) + middle + payload + end
