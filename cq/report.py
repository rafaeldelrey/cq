"""A run's report: report.md and the interactive dashboard.html, written into the run's folder.

The trend comes from history/ (every commit measured so far, up to the run's commit) with the
run's own snapshot as the last point.
"""

import json
from pathlib import Path

from cq import dashboard, findings, render_md
from cq.metrics import load_history


def latest_run(runs_dir: Path) -> Path | None:
    """The newest run folder that holds a snapshot."""
    runs = sorted(p.parent for p in runs_dir.glob("*/snapshot.json"))
    return runs[-1] if runs else None


def write_reports(
    run_dir: Path,
    history_dir: Path,
    *,
    window: int = 30,
    details: bool = True,
    analysis: Path | None = None,
    name: str = "",
) -> str:
    """Render report.md and dashboard.html into ``run_dir``; return the Markdown."""
    snap = json.loads((run_dir / "snapshot.json").read_text())
    full_history = load_history(history_dir)
    history = dashboard.history_until(full_history, snap)
    text = render_md.render(history, window_days=window, details=details)
    if len(history) > 1:
        # The flag goes right after the header, before "## Since the previous snapshot".
        header, sep, rest = text.partition("\n## ")
        notes = render_md.step_section(render_md.measurement_changes(history[-2], history[-1]))
        text = header + "\n".join(notes) + sep + rest if notes and sep else text
    (run_dir / "report.md").write_text(text)
    found = findings.mark_new(findings.from_snapshot(snap), history[-2] if len(history) > 1 else None)
    findings.write_jsonl(found, run_dir / "findings.jsonl")
    analysis_path = analysis or (run_dir / "analysis.md")
    analysis_md = analysis_path.read_text() if analysis_path.exists() else None
    title = f"{name} code quality" if name else "Code quality"
    (run_dir / "dashboard.html").write_text(dashboard.build(full_history, snap, run_dir, analysis_md, found, title))
    return text
