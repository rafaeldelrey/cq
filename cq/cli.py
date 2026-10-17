"""cq: code-quality snapshots of a repository, stored as history, reported as trends; and a PR gate.

    cq doctor                          which measuring tools are installed, and how to get the rest
    cq setup                           install them (Homebrew; qlty from its checked release)
    cq init                            starter settings and qlty's config for this repo
    cq run                             every target, every tool, at HEAD → history/ and runs/
    cq run --target app --tool ruff    partial: kept under runs/ only, not in history/
    cq run --rev a1b2c3d               a past commit, measured in a temporary worktree
    cq gate --base origin/main         what HEAD adds over its base
    cq report [--run <id>]             re-render a run's report.md and dashboard.html
    cq backfill --since 2026-06-01     past commits into history/, one every 14 days
    cq complexity check src            the cyclomatic-complexity ratchet (check | report | baseline)

``--repo`` names the measured repo (default: the current directory), ``--config`` (or ``CQ_CONFIG``)
a settings file other than its cq.toml or pyproject's [tool.cq]; relative to the repo. Runs and history go to the settings' data_dir
(default .cq/, which ignores itself).
"""

import argparse
import importlib.metadata
import json
import os
import sys
from pathlib import Path

from cq import complexity_gate
from cq.backfill import backfill, commits_to_measure
from cq.collectors import COLLECTORS
from cq.config import Settings, load_gate, load_settings
from cq.doctor import doctor, setup
from cq.gate import run_gate
from cq.init import SKILLS_CHOICES, init
from cq.report import latest_run, write_reports
from cq.runner import git, save, snapshot


def parser() -> argparse.ArgumentParser:
    """The command line."""
    p = argparse.ArgumentParser(prog="cq", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"cq {importlib.metadata.version('cq')}")
    p.add_argument("--repo", type=Path, default=Path(), help="the repository to measure (default: here)")
    p.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ["CQ_CONFIG"]) if os.environ.get("CQ_CONFIG") else None,
        help="settings file (default: $CQ_CONFIG, else cq.toml, else pyproject's [tool.cq])",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="take a snapshot")
    run.add_argument("--target", action="append", help="measure only this target (repeatable)")
    run.add_argument("--tool", action="append", choices=list(COLLECTORS), help="run only this tool (repeatable)")
    run.add_argument("--rev", default="HEAD", help="commit to measure (default HEAD, the working tree)")
    run.add_argument("--no-history", action="store_true", help="keep the snapshot under runs/ only")
    run.add_argument("--print", action="store_true", help="print the snapshot JSON on stdout")

    gate = sub.add_parser("gate", help="judge what HEAD adds over a base")
    gate.add_argument("--base", default="origin/main", help="the base ref (default origin/main)")
    gate.add_argument("--report-only", action="store_true", help="report, exit 0 even in block mode")

    report = sub.add_parser("report", help="render a run's report and dashboard")
    report.add_argument("--run", help="run folder name under runs/ (default: the latest)")
    report.add_argument("--window", type=int, default=30, help="days for the Markdown's middle comparison column")
    report.add_argument("--summary", action="store_true", help="leave the hot-spot lists out of the Markdown")
    report.add_argument(
        "--analysis", type=Path, help="Markdown analysis for the dashboard (default: <run>/analysis.md)"
    )

    back = sub.add_parser("backfill", help="measure past commits into history/")
    back.add_argument("--since", help="ISO date of the first step")
    back.add_argument("--every", type=int, default=14, help="days between steps (default 14)")
    back.add_argument("--rev", action="append", default=[], help="a specific commit (repeatable)")
    back.add_argument("--branch", default="HEAD", help="the branch --since walks (default: the checked-out one)")
    back.add_argument("--force", action="store_true", help="re-measure commits already in history/")
    back.add_argument("--dry-run", action="store_true", help="list the commits only")

    complexity = sub.add_parser("complexity", help="the cyclomatic-complexity ratchet")
    complexity_gate.add_arguments(complexity, limit=None)

    sub.add_parser("doctor", help="which measuring tools are installed")
    for name, text in (("setup", "install the missing tools"), ("init", "set this repo up for cq")):
        sub.add_parser(name, help=text).add_argument("--yes", action="store_true", help="answer yes to every question")
    sub.choices["init"].add_argument(
        "--skills",
        choices=SKILLS_CHOICES,
        help="install cq's Claude Code plugin for the user or this project, or not, without asking (--yes: user)",
    )
    return p


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    """Measure, save, render, print where it went."""
    names = {t.name for t in settings.targets}
    unknown = sorted(set(args.target or []) - names)
    if unknown:
        print(f"unknown target(s) {', '.join(unknown)}; this repo has {', '.join(sorted(names))}", file=sys.stderr)
        return 2
    print(f"Measuring {args.rev}…", file=sys.stderr)
    snap = snapshot(settings, rev=args.rev, targets=args.target, tools=args.tool)
    to_history = not snap["partial"] and not args.no_history
    for path in save(snap, settings, to_history=to_history):
        print(f"wrote {path}", file=sys.stderr)
    if snap["errors"]:
        print(f"{len(snap['errors'])} tool error(s): {', '.join(snap['errors'])}", file=sys.stderr)
    run_dir = settings.runs_dir / snap["run_id"]
    write_reports(run_dir, settings.history_dir, name=settings.name)
    print(f"report:    {run_dir / 'report.md'}\ndashboard: {run_dir / 'dashboard.html'}", file=sys.stderr)
    if args.print:
        print(json.dumps(snap, indent=1))
    return 0


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    """Pick the run, render, print."""
    run_dir = settings.runs_dir / args.run if args.run else latest_run(settings.runs_dir)
    if run_dir is None or not (run_dir / "snapshot.json").exists():
        print("No run found: take one with `cq run` first.", file=sys.stderr)
        return 1
    text = write_reports(
        run_dir,
        settings.history_dir,
        window=args.window,
        details=not args.summary,
        analysis=args.analysis,
        name=settings.name,
    )
    print(text)
    print(f"wrote {run_dir / 'report.md'}\nwrote {run_dir / 'dashboard.html'}", file=sys.stderr)
    return 0


def cmd_backfill(settings: Settings, args: argparse.Namespace) -> int:
    """Pick the commits, then measure each one."""
    if not args.since and not args.rev:
        print("give --since or --rev", file=sys.stderr)
        return 2
    if args.every < 1:
        print(f"--every must be at least 1 (days), not {args.every}", file=sys.stderr)
        return 2
    todo = commits_to_measure(settings, args.rev, args.since, args.every, force=args.force, branch=args.branch)
    for sha in todo:
        print(git("show", "-s", "--format=%h %cs %s", sha, cwd=settings.repo_root), file=sys.stderr)
    if args.dry_run or not todo:
        print(f"{len(todo)} commit(s) to measure.", file=sys.stderr)
        return 0
    backfill(settings, todo)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run one command."""
    args = parser().parse_args(argv)
    if args.command == "doctor":
        return doctor(args.repo.resolve())
    if args.command == "setup":
        return setup(assume_yes=args.yes)
    repo = args.repo.resolve()
    if not (repo / ".git").exists():
        print(f"{repo} is not the root of a git repository: run cq there, or pass --repo", file=sys.stderr)
        return 2
    os.chdir(repo)  # git and the tools run in the measured repo
    config = repo / args.config if args.config and not args.config.is_absolute() else args.config
    settings = load_settings(repo, config)
    if args.command == "init":
        return init(settings, assume_yes=args.yes, skills=args.skills)
    if args.command == "run":
        return cmd_run(settings, args)
    if args.command == "gate":
        return run_gate(settings, load_gate(settings), args.base, report_only=args.report_only)
    if args.command == "report":
        return cmd_report(settings, args)
    if args.command == "backfill":
        return cmd_backfill(settings, args)
    if args.limit is None:
        args.limit = settings.complexity_limit
    return complexity_gate.run(args, settings.repo_root, settings.repo_root / settings.complexity_baseline)


if __name__ == "__main__":
    sys.exit(main())
