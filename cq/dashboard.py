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

from cq.metrics import METRICS
from cq.render_md import measurement_changes

TEMPLATE = Path(__file__).with_name("dashboard.html")
HEADING = re.compile(r"^(#{1,4})\s+(.*)")  # the analysis' Markdown: headings and list items
LIST_ITEM = re.compile(r"^\s*([-*]|\d+\.)\s+(.*)")


def history_until(history: list[dict], snap: dict) -> list[dict]:
    """The history as it stood at this run: earlier commits, then the run's own snapshot last."""
    cutoff = datetime.fromisoformat(snap["commit_date"])
    earlier = [
        s for s in history if s["commit"] != snap["commit"] and datetime.fromisoformat(s["commit_date"]) <= cutoff
    ]
    return [*earlier, snap]


def series_point(snap: dict) -> dict:
    """One snapshot reduced to the headline metrics of each target."""
    values = {
        name: {m.path: v for m in METRICS if (v := m.read(target)) is not None}
        for name, target in snap["targets"].items()
    }
    return {
        "short": snap["short"],
        "date": snap["commit_date"],
        "subject": snap["subject"],
        "dirty": snap["dirty"],
        "values": values,
    }


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
    out = html.escape(text, quote=False)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    return out


class HtmlBuilder:
    """The HTML for markdown_to_html, built one Markdown line at a time."""

    def __init__(self) -> None:
        """An empty document: no open paragraph, no open list."""
        self.out: list[str] = []
        self.list_kind: str | None = None
        self.para: list[str] = []

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

    def blank(self) -> None:
        """A blank line ends both the paragraph and the list."""
        self.end_paragraph()
        self.end_list()

    def heading(self, m: re.Match[str]) -> None:
        """``#`` to ``####`` become h3 to h6: the dashboard's own headings come first."""
        self.blank()
        level = min(len(m[1]) + 2, 6)
        self.out.append(f"<h{level}>{inline(m[2])}</h{level}>")

    def item(self, m: re.Match[str]) -> None:
        """A list item, opening its list (numbered or not) when the kind changes."""
        self.end_paragraph()
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
        """A line of a paragraph: it ends any list."""
        self.end_list()
        self.para.append(line.strip())


def markdown_to_html(md: str) -> str:
    """The small Markdown subset the /code-quality analysis is written in: headings, lists, paragraphs."""
    html = HtmlBuilder()
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            html.blank()
        elif heading := HEADING.match(line):
            html.heading(heading)
        elif item := LIST_ITEM.match(line):
            html.item(item)
        else:
            html.text(line)
    return html.document()


def build(
    history: list[dict],
    snap: dict,
    run_dir: Path,
    analysis_md: str | None = None,
    found: list[dict] | None = None,
    title: str = "Code quality",
) -> str:
    """The dashboard page for the run whose snapshot is ``snap``, headed ``title``."""
    upto = history_until(history, snap)
    points = [series_point(s) for s in upto]
    data = {
        "metrics": [
            {"path": m.path, "label": m.label, "better": m.better, "unit": m.unit, "group": m.group} for m in METRICS
        ],
        "series": points,
        "snapshot": snap,
        "files": run_files(run_dir),
        "analysis": markdown_to_html(analysis_md) if analysis_md else None,
        "findings": found or [],
        "steps": measurement_changes(upto[-2], snap) if len(upto) > 1 else [],
    }
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text().replace("__TITLE__", html.escape(title))
    return page.replace("/*__DATA__*/{}", payload)
