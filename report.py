"""Write a run's report: report.md and the interactive dashboard.html, into the run's folder.

Usage (from code_quality/):
    uv run report.py                          # the latest run under runs/
    uv run report.py --run 20261010T212524Z_a13ebda
    uv run report.py --analysis runs/<run>/analysis.md   # add the written analysis to the dashboard
    uv run report.py --window 7 --summary     # Markdown: compare with a week ago, no hot-spot lists

The trend comes from history/ (every commit measured so far, up to the run's commit) with the
run's own snapshot as the last point. The Markdown is also printed on stdout.
"""

import argparse
import json
import sys
from pathlib import Path

from cq import dashboard, findings, render_md
from cq.config import RUNS_DIR
from cq.metrics import load_history


def latest_run() -> Path | None:
    """The newest run folder that holds a snapshot."""
    runs = sorted(p.parent for p in RUNS_DIR.glob("*/snapshot.json"))
    return runs[-1] if runs else None


def write_reports(run_dir: Path, *, window: int = 30, details: bool = True, analysis: Path | None = None) -> str:
    """Render report.md and dashboard.html into ``run_dir``; return the Markdown."""
    snap = json.loads((run_dir / "snapshot.json").read_text())
    history = dashboard.history_until(load_history(), snap)
    text = render_md.render(history, window_days=window, details=details)
    (run_dir / "report.md").write_text(text)
    found = findings.mark_new(findings.from_snapshot(snap), history[-2] if len(history) > 1 else None)
    findings.write_jsonl(found, run_dir / "findings.jsonl")
    analysis_path = analysis or (run_dir / "analysis.md")
    analysis_md = analysis_path.read_text() if analysis_path.exists() else None
    (run_dir / "dashboard.html").write_text(dashboard.build(load_history(), snap, run_dir, analysis_md, found))
    return text


def main() -> int:
    """Pick the run, render, print."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", help="run folder name under runs/ (default: the latest)")
    parser.add_argument("--window", type=int, default=30, help="days for the Markdown's middle comparison column")
    parser.add_argument("--summary", action="store_true", help="leave the hot-spot lists out of the Markdown")
    parser.add_argument(
        "--analysis", type=Path, help="Markdown analysis to show on the dashboard (default: <run>/analysis.md)"
    )
    args = parser.parse_args()

    run_dir = RUNS_DIR / args.run if args.run else latest_run()
    if run_dir is None or not (run_dir / "snapshot.json").exists():
        print("No run found: take one with `uv run run.py` first.", file=sys.stderr)
        return 1
    print(write_reports(run_dir, window=args.window, details=not args.summary, analysis=args.analysis))
    print(f"wrote {run_dir / 'report.md'}\nwrote {run_dir / 'dashboard.html'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
