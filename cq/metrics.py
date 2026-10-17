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


METRICS: tuple[Metric, ...] = (
    Metric(
        "tests.coverage.percent", "Coverage", "up", "%", group="Tests and coverage"
    ),  # Python: coverage.py's branch-aware total; UI: lines
    Metric("tests.coverage.branch_percent", "Branch coverage", "up", "%", group="Tests and coverage"),
    Metric("tests.results.passed", "Tests passing", "up", group="Tests and coverage"),
    Metric("tests.results.failed", "Tests failing", "down", group="Tests and coverage"),
    Metric("ruff.violations", "Ruff findings", "down", group="Static checks"),
    Metric("ruff.unformatted_files", "Unformatted files", "down", group="Static checks"),
    Metric("pyright.errors", "Pyright errors", "down", group="Static checks"),
    Metric("eslint.errors", "ESLint errors", "down", group="Static checks"),
    Metric("eslint.warnings", "ESLint warnings", "down", group="Static checks"),
    Metric("typecheck.errors", "tsc errors", "down", group="Static checks"),
    Metric("complexity.mean", "Mean cyclomatic complexity", "down", group="Complexity"),
    Metric("complexity.over_limit", "Functions over CC limit", "down", group="Complexity"),
    Metric("complexity.max", "Worst cyclomatic complexity", "down", group="Complexity"),
    Metric("cognitive.mean", "Mean cognitive complexity", "down", group="Complexity"),
    Metric("cognitive.over_limit", "Functions over cognitive limit", "down", group="Complexity"),
    Metric("maintainability.weighted_mean", "Maintainability index (line-weighted)", "up", group="Complexity"),
    Metric("dead_code.high", "Dead code, high confidence", "down", group="Hygiene"),
    Metric("dead_code.total", "Dead code, all candidates", "down", group="Hygiene"),
    Metric("duplication.percent", "Duplicated lines", "down", "%", group="Hygiene"),
    Metric("duplication.clones", "Clones", "down", group="Hygiene"),
    Metric("docstrings.percent", "Docstring coverage", "up", "%", group="Hygiene"),
    Metric("semgrep.errors", "Semgrep errors", "down", group="Security and conventions"),
    Metric("semgrep.findings", "Semgrep findings", "down", group="Security and conventions"),
    Metric("vulnerabilities.vulnerabilities", "Known vulnerabilities", "down", group="Security and conventions"),
    Metric("vulnerabilities.critical", "Critical vulnerabilities", "down", group="Security and conventions"),
    Metric("vulnerabilities.high", "High vulnerabilities", "down", group="Security and conventions"),
    Metric("secrets.in_tree", "Secrets in the current tree", "down", group="Security and conventions"),
    Metric("secrets.in_history", "Secrets in git history", "down", group="Security and conventions"),
    Metric("secrets.verified_live", "Live keys in git history (verified)", "down", group="Security and conventions"),
    Metric(
        "scripts.workflow_security.findings",
        "Workflow security findings (zizmor)",
        "down",
        group="Security and conventions",
    ),
    Metric("imports.broken", "Architecture contracts broken", "down", group="Architecture"),
    Metric("imports.violations", "Contract-breaking imports", "down", group="Architecture"),
    Metric("dependencies.issues", "Dependency issues", "down", group="Architecture"),
    Metric("mutation.score", "Mutation score", "up", "%", group="Test strength"),
    Metric("mutation.survived", "Surviving mutants", "down", group="Test strength"),
    Metric("semgrep.mock_only_tests", "Tests that only assert mocks", "down", group="Tests and coverage"),
    Metric("hotspots.hotspots", "Hotspots (churn × complexity)", "down", group="Complexity"),
    Metric("hotspots.top_score", "Top hotspot score", "down", group="Complexity"),
    Metric("containers.image.critical", "Image vulnerabilities, critical", "down", group="Security and conventions"),
    Metric("containers.image.high", "Image vulnerabilities, high", "down", group="Security and conventions"),
    Metric(
        "containers.image.fixable_high",
        "Image high vulnerabilities with a fix",
        "down",
        group="Security and conventions",
    ),
    Metric("containers.dockerfile.findings", "Dockerfile findings (hadolint)", "down", group="Hygiene"),
    Metric("suppressions.total", "Suppression comments", "down", group="Hygiene"),
    Metric("suppressions.without_reason", "Suppressions without a reason", "down", group="Hygiene"),
    Metric("scripts.shell.findings", "Shell script findings (shellcheck)", "down", group="Hygiene"),
    Metric("scripts.workflows.findings", "Workflow findings (actionlint)", "down", group="Hygiene"),
    Metric("links.broken", "Broken links in the docs", "down", group="Hygiene"),
    Metric("radarlint.findings", "SonarSource rule findings (radarlint)", "down", group="AI slop and structure"),
    Metric("smells.structure", "Structure smells (qlty)", "down", group="AI slop and structure"),
    Metric("smells.duplication", "Similar or identical blocks (qlty)", "down", group="AI slop and structure"),
    Metric("aislop.slop", "AI-slop findings (aislop)", "down", group="AI slop and structure"),
    Metric("aislop.score", "aislop score", "up", group="AI slop and structure"),
    Metric("pyscn.clone_pairs", "Renamed copy-paste, Python (pyscn)", "down", group="AI slop and structure"),
    Metric(
        "pyscn.duplication_percent", "Near-duplicate code, Python (pyscn)", "down", "%", group="AI slop and structure"
    ),
    Metric(
        "fallow.duplication_percent", "Near-duplicate code, UI (fallow)", "down", "%", group="AI slop and structure"
    ),
    Metric("fallow.critical", "UI functions in critical health (fallow)", "down", group="AI slop and structure"),
    Metric("vale.findings", "AI-prose and stub language (vale)", "down", group="AI slop and structure"),
    Metric("cspell.unknown_words", "Unknown words in code (cspell)", "down", group="Hygiene"),
    Metric("freshness.libyear", "Dependency lag (libyears)", "down", group="Dependencies"),
    Metric("freshness.outdated", "Outdated direct dependencies", "down", group="Dependencies"),
    Metric("freshness.major_behind", "A major version behind", "down", group="Dependencies"),
    Metric("size.sloc", "Source lines (SLOC)", "none", group="Size"),
    Metric("duplication.lines", "Lines scanned", "none", group="Size"),
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
