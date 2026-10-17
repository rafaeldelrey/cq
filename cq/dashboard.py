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

TEMPLATE = Path(__file__).with_name("dashboard.html")


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


def markdown_to_html(md: str) -> str:
    """The small Markdown subset the /code-quality analysis is written in: headings, lists, paragraphs."""
    out: list[str] = []
    in_list: str | None = None
    para: list[str] = []

    def flush() -> None:
        nonlocal in_list
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    for raw in md.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^(#{1,4})\s+(.*)", line)
        item = re.match(r"^\s*([-*]|\d+\.)\s+(.*)", line)
        if not line.strip():
            flush()
        elif heading:
            flush()
            level = min(len(heading[1]) + 2, 6)
            out.append(f"<h{level}>{inline(heading[2])}</h{level}>")
        elif item:
            if para:
                out.append(f"<p>{inline(' '.join(para))}</p>")
                para.clear()
            kind = "ol" if item[1][0].isdigit() else "ul"
            if in_list != kind:
                if in_list:
                    out.append(f"</{in_list}>")
                out.append(f"<{kind}>")
                in_list = kind
            out.append(f"<li>{inline(item[2])}</li>")
        else:
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            para.append(line.strip())
    flush()
    return "\n".join(out)


def build(
    history: list[dict],
    snap: dict,
    run_dir: Path,
    analysis_md: str | None = None,
    found: list[dict] | None = None,
    title: str = "Code quality",
) -> str:
    """The dashboard page for the run whose snapshot is ``snap``, headed ``title``."""
    points = [series_point(s) for s in history_until(history, snap)]
    data = {
        "metrics": [
            {"path": m.path, "label": m.label, "better": m.better, "unit": m.unit, "group": m.group} for m in METRICS
        ],
        "series": points,
        "snapshot": snap,
        "files": run_files(run_dir),
        "analysis": markdown_to_html(analysis_md) if analysis_md else None,
        "findings": found or [],
    }
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text().replace("__TITLE__", html.escape(title))
    return page.replace("/*__DATA__*/{}", payload)
