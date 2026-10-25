"""The published-benchmark ratings: every cut point, the overall grade, and where the report and dashboard show them."""

import json
from pathlib import Path

import pytest

from cq import benchmarks, dashboard, render_md
from cq.metrics import METRICS, Metric, load_history


def metric(path: str) -> Metric:
    """A metric from the registry by path."""
    return next(m for m in METRICS if m.path == path)


def rated(path: str, value: float) -> tuple[str, str]:
    """The label and band ``value`` gets on the metric at ``path``."""
    rating = benchmarks.rate(metric(path), value)
    assert rating is not None
    return rating.label, rating.band


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100, ("Excellent", "Google: exemplary")),
        (90, ("Excellent", "Google: exemplary")),
        (89.99, ("Good", "Google: commendable")),
        (75, ("Good", "Google: commendable")),
        (74.99, ("Fair", "Google: acceptable")),
        (60, ("Fair", "Google: acceptable")),
        (59.99, ("Poor", "under Google's 60")),
        (20, ("Poor", "under Google's 60")),
        (19.99, ("Critical", "SIG: under 20")),
        (0, ("Critical", "SIG: under 20")),
    ],
)
def test_coverage_bands(value: float, expected: tuple[str, str]) -> None:
    assert rated("tests.coverage.percent", value) == expected


@pytest.mark.parametrize("path", ["complexity.max", "complexity.mean"])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, ("Excellent", "radon A")),
        (5, ("Excellent", "radon A")),
        (5.01, ("Good", "radon B")),
        (10, ("Good", "radon B")),
        (11, ("Fair", "radon C")),
        (20, ("Fair", "radon C")),
        (21, ("Poor", "radon D")),
        (30, ("Poor", "radon D")),
        (31, ("Critical", "radon E")),
        (40, ("Critical", "radon E")),
        (41, ("Critical", "radon F")),
    ],
)
def test_cyclomatic_complexity_follows_radons_ranks(path: str, value: float, expected: tuple[str, str]) -> None:
    assert rated(path, value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100, ("Good", "radon A")),
        (19.5, ("Good", "radon A")),
        (19, ("Fair", "radon B")),
        (10, ("Fair", "radon B")),
        (9.5, ("Fair", "radon B")),
        (9, ("Poor", "radon C")),
        (0, ("Poor", "radon C")),
    ],
)
def test_maintainability_follows_radons_ranks(value: float, expected: tuple[str, str]) -> None:
    assert rated("maintainability.weighted_mean", value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, ("Excellent", "SIG ++")),
        (3, ("Excellent", "SIG ++")),
        (3.01, ("Good", "SIG +")),
        (5, ("Good", "SIG +")),
        (10, ("Fair", "SIG o")),
        (20, ("Poor", "SIG -")),
        (20.01, ("Critical", "SIG --")),
    ],
)
def test_duplication_bands(value: float, expected: tuple[str, str]) -> None:
    assert rated("duplication.percent", value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (80, ("Good", "Stryker: high")),
        (79.99, ("Fair", "Stryker: low")),
        (60, ("Fair", "Stryker: low")),
        (59.99, ("Poor", "Stryker: under low")),
    ],
)
def test_mutation_score_bands(value: float, expected: tuple[str, str]) -> None:
    assert rated("mutation.score", value) == expected


def test_only_the_metrics_with_a_published_scale_are_rated() -> None:
    rated_paths = {m.path for m in METRICS if benchmarks.rate(m, 1.0) is not None}
    assert rated_paths == {
        "tests.coverage.percent",
        "complexity.max",
        "complexity.mean",
        "maintainability.weighted_mean",
        "duplication.percent",
        "mutation.score",
    }
    assert benchmarks.rate(metric("tests.coverage.percent"), None) is None  # not measured: no rating


def test_a_value_that_is_not_a_number_gets_no_rating() -> None:
    assert benchmarks.rate(metric("tests.coverage.percent"), float("nan")) is None
    assert benchmarks.rate(metric("duplication.percent"), float("nan")) is None


def target(coverage: float | None = None, worst: float | None = None, mean: float | None = None) -> dict:
    """One target's measurements, with only the given metrics measured."""
    out: dict = {"tests": {"coverage": {}}, "complexity": {}}
    if coverage is not None:
        out["tests"]["coverage"]["percent"] = coverage
    if worst is not None:
        out["complexity"]["max"] = worst
    if mean is not None:
        out["complexity"]["mean"] = mean
    return out


def test_the_grade_is_the_rounded_mean_and_names_the_weakest_metric() -> None:
    found = benchmarks.grade(target(coverage=97, worst=15, mean=4.2))  # 5, 3, 5: mean 4.33
    assert found is not None
    assert (found.label, found.rated) == ("Good", 3)
    assert (found.weakest.label, found.weakest_rating.label) == ("Worst cyclomatic complexity", "Fair")
    assert found.text() == (
        "**Grade: Good**, the mean of 3 rated metrics; weakest: Worst cyclomatic complexity, Fair (radon C)"
    )


def test_the_grade_rounds_half_up() -> None:
    found = benchmarks.grade(target(coverage=97, worst=25))  # 5 and 2: mean 3.5
    assert found is not None
    assert found.label == "Good"
    below = benchmarks.grade(target(coverage=97, worst=25, mean=25))  # 5, 2, 2: mean 3
    assert below is not None
    assert below.label == "Fair"


def test_a_best_band_of_good_still_reaches_excellent() -> None:
    measured = target(coverage=97, worst=5, mean=4)  # 5, 5, 5
    measured["maintainability"] = {"weighted_mean": 55.0}  # radon A, whose label is Good: 4
    found = benchmarks.grade(measured)
    assert found is not None
    assert found.label == "Excellent"  # 4.75


def test_one_rated_metric_is_no_grade() -> None:
    assert benchmarks.grade(target(coverage=97)) is None
    assert benchmarks.grade({}) is None


def snapshot(day: str, sha: str, measured: dict) -> dict:
    """A minimal snapshot of one target, ``app``."""
    return {
        "schema": 1,
        "run_id": f"x_{sha}",
        "run_at": f"{day}T12:00:00+00:00",
        "commit": sha * 6,
        "short": sha,
        "commit_date": f"{day}T10:00:00+00:00",
        "subject": "subject",
        "dirty": False,
        "partial": False,
        "tool_versions": {},
        "errors": {},
        "targets": {"app": measured},
    }


def history_of(tmp_path: Path, *snaps: dict) -> list[dict]:
    """Store snapshots in a history folder and load them back."""
    for s in snaps:
        (tmp_path / f"{s['commit_date'][:10]}_{s['short']}.json").write_text(json.dumps(s))
    return load_history(tmp_path)


def test_the_report_rates_each_rated_metric_and_grades_the_target(tmp_path: Path) -> None:
    measured = target(coverage=97.16, worst=15, mean=4.25)
    measured["ruff"] = {"violations": 3}
    text = render_md.render(history_of(tmp_path, snapshot("2026-10-24", "bbb", measured)))
    assert "| Metric | Now | Rating | Δ prev | Δ 30d | Δ first | Trend |" in text
    assert "| Coverage | 97.16% | Excellent · Google: exemplary |" in text
    assert "| Worst cyclomatic complexity | 15 | Fair · radon C |" in text
    assert "| Ruff findings | 3 |  |" in text  # no published scale: blank
    assert (
        "**Grade: Good**, the mean of 3 rated metrics; weakest: Worst cyclomatic complexity, Fair (radon C).\n" in text
    )
    assert (
        "Ratings: published benchmarks in cq's own units; the sources are in "
        "references/benchmarks.md in the cq plugin's /cq skill." in text
    )


def test_a_target_with_one_rated_metric_has_no_grade_line(tmp_path: Path) -> None:
    text = render_md.render(history_of(tmp_path, snapshot("2026-10-24", "bbb", target(coverage=50))))
    assert "| Coverage | 50.00% | Poor · under Google's 60 |" in text
    assert "Grade:" not in text
    assert "Ratings: published benchmarks" in text  # a rating still names its sources


def test_a_target_with_no_rated_metric_has_no_ratings_note(tmp_path: Path) -> None:
    text = render_md.render(history_of(tmp_path, snapshot("2026-10-24", "bbb", {"ruff": {"violations": 3}})))
    assert "| Ruff findings | 3 |  |" in text
    assert "Ratings:" not in text


def test_a_snapshot_without_the_newer_metrics_rates_what_it_has(tmp_path: Path) -> None:
    measured = target(coverage=70)
    measured["maintainability"] = {"weighted_mean": 55.0}  # an older snapshot: no complexity mean or max
    text = render_md.render(history_of(tmp_path, snapshot("2026-09-01", "aaa", measured)))
    assert "| Coverage | 70.00% | Fair · Google: acceptable |" in text
    assert "| Maintainability index (line-weighted) | 55 | Good · radon A |" in text
    assert "**Grade: Good**, the mean of 2 rated metrics; weakest: Coverage, Fair" in text  # 3 and 4: 3.5


def test_the_dashboard_carries_each_rating_and_the_grade(tmp_path: Path) -> None:
    snap = snapshot("2026-10-24", "bbb", target(coverage=97.16, worst=15, mean=4.25))
    (tmp_path / "history").mkdir()
    history = history_of(tmp_path / "history", snap)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    page = dashboard.build(history, history[-1], run_dir, report_md=render_md.render(history))
    data = json.loads(page.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0])
    app = data["ratings"]["app"]
    assert app["metrics"]["tests.coverage.percent"] == {"label": "Excellent", "band": "Google: exemplary"}
    assert app["metrics"]["complexity.max"] == {"label": "Fair", "band": "radon C"}
    assert "ruff.violations" not in app["metrics"]
    assert app["grade"] == {"label": "Good", "rated": 3, "weakest": "Worst cyclomatic complexity"}
    assert data["reference"] == "references/benchmarks.md in the cq plugin's /cq skill"
    assert "Fair · radon C" in page  # the static report, for a page whose script doesn't run


def test_a_target_without_a_grade_has_none_in_the_dashboard(tmp_path: Path) -> None:
    assert dashboard.ratings(snapshot("2026-10-24", "bbb", target(coverage=50)))["app"]["grade"] is None
