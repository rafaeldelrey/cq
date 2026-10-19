"""Test results and coverage: pytest + coverage for Python apps, vitest for the UI.

Python coverage uses the app's own [tool.coverage] config (branch coverage, its ``source``), the
number the app's own coverage gate uses (``fail_under``).
"""

import json
import re
import tomllib

from cq.context import Context
from cq.proc import ToolError, run, run_app_tool

KINDS = ("python", "typescript")

SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|deselected|xfailed|xpassed)")
SUMMARY_TIME = re.compile(r" in ([\d.]+)s")


def parse_pytest_summary(stdout: str) -> dict:
    """Counts and duration from pytest's last summary line ("1590 passed, 29 deselected in 9.40s")."""
    lines = [line for line in stdout.splitlines() if SUMMARY_TIME.search(line)]
    if not lines:
        raise ToolError(f"no pytest summary line in output: {stdout[-500:]}")
    last = lines[-1]
    counts: dict[str, float | None] = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "deselected": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    for n, word in SUMMARY_COUNT.findall(last):
        key = "errors" if word.startswith("error") else word
        counts[key] = (counts[key] or 0) + int(n)
    time = SUMMARY_TIME.search(last)
    counts["duration_s"] = float(time.group(1)) if time else None
    return counts


def fail_under(ctx: Context) -> float | None:
    """The app's coverage floor from its pyproject, if it sets one."""
    pyproject = ctx.app_dir / "pyproject.toml"
    if not pyproject.exists():
        return None
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- optional TOML tables: most pyproject files have no [tool.coverage.report]
    report = tomllib.loads(pyproject.read_text()).get("tool", {}).get("coverage", {}).get("report", {})
    value = report.get("fail_under")
    return float(value) if value is not None else None


def coverage_summary(data: dict, ctx: Context) -> dict:
    """Totals from coverage.json, plus the least-covered files."""
    totals = data["totals"]
    statements, missing = totals["num_statements"], totals["missing_lines"]
    branches, missing_branches = totals.get("num_branches", 0), totals.get("missing_branches", 0)
    files = [
        {
            "file": ctx.rel(name),
            "percent": round(f["summary"]["percent_covered"], 2),
            "missing": f["summary"]["missing_lines"],
        }
        for name, f in data["files"].items()
    ]
    files.sort(key=lambda f: (f["percent"], -f["missing"]))
    return {
        "percent": round(totals["percent_covered"], 2),
        "line_percent": round(100 * (statements - missing) / statements, 2) if statements else None,
        "branch_percent": round(100 * (branches - missing_branches) / branches, 2) if branches else None,
        "statements": statements,
        "missing_lines": missing,
        "branches": branches,
        "missing_branches": missing_branches,
        "fail_under": fail_under(ctx),
        "worst_files": [f for f in files if f["missing"] > 0][:15],
    }


def collect_python(ctx: Context) -> dict:
    """pytest with branch coverage; integration tests stay excluded by each app's addopts."""
    cov_json = ctx.raw_dir / "coverage.json"
    out = run_app_tool(
        ("pytest",),
        ["pytest", "-q", "-p", "no:cacheprovider", "--cov", f"--cov-report=json:{cov_json}", "--cov-report="],
        ctx.app_dir,
        ok_codes=(0, 1),  # 1: some tests failed — counted, not an error of the measurement
        env={"COVERAGE_FILE": str(ctx.raw_dir / ".coverage")},
        # pytest-cov is overlaid: older commits ran tests without it, and its version doesn't move the numbers.
        overlay=("pytest-cov",),
    )
    (ctx.raw_dir / "pytest.txt").write_text(out)
    result = {"results": parse_pytest_summary(out)}
    if cov_json.exists():
        result["coverage"] = coverage_summary(json.loads(cov_json.read_text()), ctx)
    return result


THRESHOLD = re.compile(r"thresholds:\s*{(?P<body>[^}]*)}")
THRESHOLD_ITEM = re.compile(r"(\w+):\s*([\d.]+)")


def vitest_floor(ctx: Context) -> dict[str, float]:
    """The ``coverage.thresholds`` the UI's vitest config sets, if any (its fail_under)."""
    configs = sorted(ctx.app_dir.glob("vitest.config.*"))
    m = THRESHOLD.search(configs[0].read_text()) if configs else None
    return {k: float(v) for k, v in THRESHOLD_ITEM.findall(m["body"])} if m else {}


def vitest_coverage(summary: dict, ctx: Context) -> dict:
    """coverage-summary.json in the same shape as the Python apps' coverage.

    ``percent`` is line coverage here: istanbul reports lines and branches separately, where
    coverage.py folds branches into one total.
    """
    total = summary["total"]
    files = []
    for name, f in summary.items():
        if name == "total":
            continue
        missing = f["lines"]["total"] - f["lines"]["covered"]
        files.append({"file": ctx.rel(name), "percent": round(float(f["lines"]["pct"]), 2), "missing": missing})
    files.sort(key=lambda f: (f["percent"], -f["missing"]))
    floor = vitest_floor(ctx)
    return {
        "percent": round(float(total["lines"]["pct"]), 2),
        "line_percent": round(float(total["lines"]["pct"]), 2),
        "branch_percent": round(float(total["branches"]["pct"]), 2),
        "function_percent": round(float(total["functions"]["pct"]), 2),
        "statements": total["statements"]["total"],
        "missing_lines": total["lines"]["total"] - total["lines"]["covered"],
        "branches": total["branches"]["total"],
        "missing_branches": total["branches"]["total"] - total["branches"]["covered"],
        "fail_under": floor.get("lines"),
        "branch_floor": floor.get("branches"),
        "worst_files": [f for f in files if f["missing"] > 0][:15],
    }


def collect_typescript(ctx: Context) -> dict:
    """vitest's JSON reporter, plus coverage when the app has a coverage provider installed."""
    if not (ctx.app_dir / "node_modules").exists():
        raise ToolError("node_modules missing; run `npm ci` in the app first")
    out_file = ctx.raw_dir / "vitest.json"
    cov_dir = ctx.raw_dir / "coverage"
    with_coverage = (ctx.app_dir / "node_modules" / "@vitest" / "coverage-v8").exists()
    coverage_args = (
        [
            "--coverage.enabled=true",
            "--coverage.reporter=json-summary",
            "--coverage.reporter=json",
            f"--coverage.reportsDirectory={cov_dir}",
        ]
        if with_coverage
        else []
    )
    # Exit 1: a test failed or coverage fell under the floor; both are measurements.
    run(
        ["npx", "vitest", "run", "--reporter=json", f"--outputFile={out_file}", *coverage_args],
        ctx.app_dir,
        ok_codes=(0, 1),
    )
    data = json.loads(out_file.read_text())
    duration = None
    if data.get("testResults"):
        ends = [r.get("endTime", 0) for r in data["testResults"]]
        duration = round((max(ends) - data["startTime"]) / 1000, 2) if ends else None
    result: dict = {
        "results": {
            "passed": data["numPassedTests"],
            "failed": data["numFailedTests"],
            "skipped": data["numPendingTests"] + data.get("numTodoTests", 0),
            "errors": data.get("numRuntimeErrorTestSuites", 0),
            "duration_s": duration,
        }
    }
    summary = cov_dir / "coverage-summary.json"
    if summary.exists():
        result["coverage"] = vitest_coverage(json.loads(summary.read_text()), ctx)
    return result


def collect(ctx: Context) -> dict:
    """Run the target's tests, if it has any."""
    if not ctx.target.tests:
        return {"skipped": "target has no tests"}
    return collect_python(ctx) if ctx.target.kind == "python" else collect_typescript(ctx)
