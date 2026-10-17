"""Take a code-quality snapshot of HanBot's apps and store it.

Usage (from code_quality/):
    uv run run.py                          # every target, every tool, at HEAD → history/
    uv run run.py --target agents_server   # partial: kept under runs/ only, not in history/
    uv run run.py --tool dead_code --tool duplication
    uv run run.py --rev a1b2c3d            # a past commit, measured in a temporary worktree
    uv run run.py --gate --base origin/main  # the PR gate: what HEAD adds over its base (cq/gate.py)

A complete run writes history/<commit-date>_<sha>.json (the trend). Every run writes
runs/<run_id>/: snapshot.json, run.log, report.md, dashboard.html, and per
app each tool's raw output (ruff.json, pyright.json, coverage.json, vulture.txt, jscpd/, ...)
and its log (<tool>.log: every command, exit code, stderr). Both folders are gitignored.
"""

import argparse
import json
import sys

from cq.collectors import COLLECTORS
from cq.config import RUNS_DIR, load_gate, load_settings
from cq.gate import run_gate
from cq.runner import save, snapshot
from report import write_reports


def main() -> int:
    """Parse arguments, measure, save, print where it went."""
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", action="append", choices=[t.name for t in settings.targets])
    parser.add_argument("--tool", action="append", choices=list(COLLECTORS))
    parser.add_argument("--rev", default="HEAD", help="commit to measure (default HEAD, the working tree)")
    parser.add_argument("--no-history", action="store_true", help="keep the snapshot under runs/ only")
    parser.add_argument("--print", action="store_true", help="print the snapshot JSON on stdout")
    parser.add_argument("--gate", action="store_true", help="judge what HEAD adds over --base (the PR gate)")
    parser.add_argument("--base", default="origin/main", help="the gate's base ref (default origin/main)")
    parser.add_argument("--report-only", action="store_true", help="gate: report, exit 0 even in block mode")
    args = parser.parse_args()
    if args.gate:
        return run_gate(settings, load_gate(), args.base, report_only=args.report_only)

    print(f"Measuring {args.rev}…", file=sys.stderr)
    snap = snapshot(settings, rev=args.rev, targets=args.target, tools=args.tool)
    to_history = not snap["partial"] and not args.no_history
    for path in save(snap, to_history=to_history):
        print(f"wrote {path}", file=sys.stderr)
    if snap["errors"]:
        print(f"{len(snap['errors'])} tool error(s): {', '.join(snap['errors'])}", file=sys.stderr)
    run_dir = RUNS_DIR / snap["run_id"]
    write_reports(run_dir)
    print(f"report:    {run_dir / 'report.md'}\ndashboard: {run_dir / 'dashboard.html'}", file=sys.stderr)
    if args.print:
        print(json.dumps(snap, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
