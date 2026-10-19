"""The headline metrics the report tracks, and reading them out of the history."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Metric:
    """One number read from a snapshot, and which way is better."""

    path: str  # "<tool>.<key>[.<key>]" inside a target
    label: str
    better: str  # "up" | "down" | "none"
    unit: str = ""
    group: str = ""

    def read(self, target: dict) -> float | None:
        """The value in one target's measurements, or None when that tool did not run."""
        node: object = target
        for key in self.path.split("."):
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return float(node) if isinstance(node, (int, float)) else None


# The dashboard's sections, in the order metrics are listed.
TESTS = "Tests and coverage"
STATIC = "Static checks"
COMPLEXITY = "Complexity"
SECURITY = "Security and conventions"
SLOP = "AI slop and structure"
ARCHITECTURE = "Architecture"
DEPENDENCIES = "Dependencies"
HYGIENE = "Hygiene"
SIZE = "Size"
TEST_STRENGTH = "Test strength"

METRICS: tuple[Metric, ...] = (
    Metric(
        "tests.coverage.percent", "Coverage", "up", "%", group=TESTS
    ),  # Python: coverage.py's branch-aware total; UI: lines
    Metric("tests.coverage.branch_percent", "Branch coverage", "up", "%", group=TESTS),
    Metric("tests.results.passed", "Tests passing", "up", group=TESTS),
    Metric("tests.results.failed", "Tests failing", "down", group=TESTS),
    Metric("ruff.violations", "Ruff findings", "down", group=STATIC),
    Metric("ruff.unformatted_files", "Unformatted files", "down", group=STATIC),
    Metric("pyright.errors", "Pyright errors", "down", group=STATIC),
    Metric("eslint.errors", "ESLint errors", "down", group=STATIC),
    Metric("eslint.warnings", "ESLint warnings", "down", group=STATIC),
    Metric("typecheck.errors", "tsc errors", "down", group=STATIC),
    Metric("complexity.mean", "Mean cyclomatic complexity", "down", group=COMPLEXITY),
    Metric("complexity.over_limit", "Functions over CC limit", "down", group=COMPLEXITY),
    Metric("complexity.max", "Worst cyclomatic complexity", "down", group=COMPLEXITY),
    Metric("cognitive.mean", "Mean cognitive complexity", "down", group=COMPLEXITY),
    Metric("cognitive.over_limit", "Functions over cognitive limit", "down", group=COMPLEXITY),
    Metric("maintainability.weighted_mean", "Maintainability index (line-weighted)", "up", group=COMPLEXITY),
    Metric("dead_code.high", "Dead code, high confidence", "down", group=HYGIENE),
    Metric("dead_code.total", "Dead code, all candidates", "down", group=HYGIENE),
    Metric("duplication.percent", "Duplicated lines", "down", "%", group=HYGIENE),
    Metric("duplication.clones", "Clones", "down", group=HYGIENE),
    Metric("docstrings.percent", "Docstring coverage", "up", "%", group=HYGIENE),
    Metric("semgrep.errors", "Semgrep errors", "down", group=SECURITY),
    Metric("semgrep.findings", "Semgrep findings", "down", group=SECURITY),
    Metric("vulnerabilities.vulnerabilities", "Known vulnerabilities", "down", group=SECURITY),
    Metric("vulnerabilities.critical", "Critical vulnerabilities", "down", group=SECURITY),
    Metric("vulnerabilities.high", "High vulnerabilities", "down", group=SECURITY),
    Metric("secrets.in_tree", "Secrets in the current tree", "down", group=SECURITY),
    Metric("secrets.in_history", "Secrets in git history", "down", group=SECURITY),
    Metric("secrets.verified_live", "Live keys in git history (verified)", "down", group=SECURITY),
    Metric(
        "scripts.workflow_security.findings",
        "Workflow security findings (zizmor)",
        "down",
        group=SECURITY,
    ),
    Metric("imports.broken", "Architecture contracts broken", "down", group=ARCHITECTURE),
    Metric("imports.violations", "Contract-breaking imports", "down", group=ARCHITECTURE),
    Metric("dependencies.issues", "Dependency issues", "down", group=ARCHITECTURE),
    Metric("mutation.score", "Mutation score", "up", "%", group=TEST_STRENGTH),
    Metric("mutation.survived", "Surviving mutants", "down", group=TEST_STRENGTH),
    Metric("semgrep.mock_only_tests", "Tests that only assert mocks", "down", group=TESTS),
    Metric("hotspots.hotspots", "Hotspots (churn × complexity)", "down", group=COMPLEXITY),
    Metric("hotspots.top_score", "Top hotspot score", "down", group=COMPLEXITY),
    Metric("containers.image.critical", "Image vulnerabilities, critical", "down", group=SECURITY),
    Metric("containers.image.high", "Image vulnerabilities, high", "down", group=SECURITY),
    Metric(
        "containers.image.fixable_high",
        "Image high vulnerabilities with a fix",
        "down",
        group=SECURITY,
    ),
    Metric("containers.dockerfile.findings", "Dockerfile findings (hadolint)", "down", group=HYGIENE),
    Metric("suppressions.total", "Suppression comments", "down", group=HYGIENE),
    Metric("suppressions.without_reason", "Suppressions without a reason", "down", group=HYGIENE),
    Metric("scripts.shell.findings", "Shell script findings (shellcheck)", "down", group=HYGIENE),
    Metric("scripts.workflows.findings", "Workflow findings (actionlint)", "down", group=HYGIENE),
    Metric("links.broken", "Broken links in the docs", "down", group=HYGIENE),
    Metric("radarlint.findings", "SonarSource rule findings (radarlint)", "down", group=SLOP),
    Metric("smells.structure", "Structure smells (qlty)", "down", group=SLOP),
    Metric("smells.duplication", "Similar or identical blocks (qlty)", "down", group=SLOP),
    Metric("aislop.slop", "AI-slop findings (aislop)", "down", group=SLOP),
    Metric("aislop.score", "aislop score", "up", group=SLOP),
    Metric("pyscn.clone_pairs", "Renamed copy-paste, Python (pyscn)", "down", group=SLOP),
    Metric("pyscn.duplication_percent", "Near-duplicate code, Python (pyscn)", "down", "%", group=SLOP),
    Metric("fallow.duplication_percent", "Near-duplicate code, UI (fallow)", "down", "%", group=SLOP),
    Metric("fallow.critical", "UI functions in critical health (fallow)", "down", group=SLOP),
    Metric("vale.findings", "AI-prose and stub language (vale)", "down", group=SLOP),
    Metric("cspell.unknown_words", "Unknown words in code (cspell)", "down", group=HYGIENE),
    Metric("freshness.libyear", "Dependency lag (libyears)", "down", group=DEPENDENCIES),
    Metric("freshness.outdated", "Outdated direct dependencies", "down", group=DEPENDENCIES),
    Metric("freshness.major_behind", "A major version behind", "down", group=DEPENDENCIES),
    Metric("size.sloc", "Source lines (SLOC)", "none", group=SIZE),
    Metric("duplication.lines", "Lines scanned", "none", group=SIZE),
)


def load_history(history_dir: Path) -> list[dict]:
    """Every stored snapshot, oldest commit first."""
    snaps = [json.loads(p.read_text()) for p in history_dir.glob("*.json")]
    return sorted(snaps, key=lambda s: (s["commit_date"], s["run_at"]))


def series(history: list[dict], target: str, metric: Metric) -> list[tuple[str, float]]:
    """(commit date, value) for every snapshot where the metric was measured."""
    out = []
    for snap in history:
        value = metric.read(snap["targets"].get(target, {}))
        if value is not None:
            out.append((snap["commit_date"], value))
    return out


def value_at_or_before(points: list[tuple[str, float]], when: datetime) -> float | None:
    """The last value measured on a commit at or before ``when``."""
    before = [v for d, v in points if datetime.fromisoformat(d) <= when]
    return before[-1] if before else None


def verdict(metric: Metric, delta: float | None) -> str:
    """ "better", "worse", "same" or "" (no direction, or nothing to compare)."""
    if delta is None or metric.better == "none":
        return ""
    if abs(delta) < 1e-9:
        return "same"
    good = delta > 0 if metric.better == "up" else delta < 0
    return "better" if good else "worse"


@dataclass(frozen=True)
class Row:
    """One metric of one target, with its deltas."""

    target: str
    metric: Metric
    now: float
    prev: float | None
    window: float | None  # value ``window_days`` ago
    first: float | None
    points: list[tuple[str, float]]

    def delta(self, other: float | None) -> float | None:
        """``now`` minus ``other``."""
        return None if other is None else self.now - other


def rows_for(history: list[dict], target: str, window_days: int) -> list[Row]:
    """The report rows of one target: every metric measured in the latest snapshot."""
    rows = []
    latest = history[-1]
    since = datetime.fromisoformat(latest["commit_date"]) - timedelta(days=window_days)
    for metric in METRICS:
        points = series(history, target, metric)
        now = metric.read(latest["targets"].get(target, {}))
        if now is None:
            continue
        earlier = points[:-1] if points and points[-1][0] == latest["commit_date"] else points
        rows.append(
            Row(
                target=target,
                metric=metric,
                now=now,
                prev=earlier[-1][1] if earlier else None,
                window=value_at_or_before(earlier, since),
                first=points[0][1] if len(points) > 1 else None,
                points=points,
            )
        )
    return rows
