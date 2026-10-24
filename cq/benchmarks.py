"""Where a metric's level sits against a published benchmark, and each target's overall grade.

The cut points and their sources are described in skills/cq/references/benchmarks.md. Only a
metric with a published standard in cq's own unit gets a rating: every other one is None, never
a guess. Ratings are computed when a report renders, from the snapshot's values, so nothing is
stored and old history rates the same way.
"""

from collections.abc import Callable
from dataclasses import dataclass

from radon.complexity import cc_rank
from radon.metrics import mi_rank

from cq.metrics import METRICS, Metric

LABELS = ("Critical", "Poor", "Fair", "Good", "Excellent")  # score 1 to 5, SIG's five-point scale
REFERENCE = "skills/cq/references/benchmarks.md"


@dataclass(frozen=True)
class Rating:
    """One metric's label, and the band it falls in as its source names it ("radon B")."""

    label: str
    band: str

    @property
    def score(self) -> int:
        """1 (Critical) to 5 (Excellent)."""
        return LABELS.index(self.label) + 1

    def text(self) -> str:
        """The label with its band, as the report writes it: "Good · radon B"."""
        return f"{self.label} · {self.band}"


def at_least(bands: tuple[tuple[float, str, str], ...]) -> Callable[[float], Rating]:
    """A higher-is-better scale: the first band whose floor the value reaches."""

    def band_of(value: float) -> Rating:
        """The band ``value`` falls in."""
        for floor, label, band in bands:
            if value >= floor:
                return Rating(label, band)
        raise ValueError(f"{value} is below every band")  # only NaN: the last floor is -inf

    return band_of


def at_most(bands: tuple[tuple[float, str, str], ...]) -> Callable[[float], Rating]:
    """A lower-is-better scale: the first band whose ceiling the value stays within."""

    def band_of(value: float) -> Rating:
        """The band ``value`` falls in."""
        for ceiling, label, band in bands:
            if value <= ceiling:
                return Rating(label, band)
        raise ValueError(f"{value} is above every band")  # only NaN: the last ceiling is inf

    return band_of


CC_LABELS = {"A": "Excellent", "B": "Good", "C": "Fair", "D": "Poor", "E": "Critical", "F": "Critical"}
MI_LABELS = {"A": "Good", "B": "Fair", "C": "Poor"}  # Visual Studio: good, moderate, low maintainability


def complexity(value: float) -> Rating:
    """radon's rank of a cyclomatic complexity: A 1-5, B 6-10, C 11-20, D 21-30, E 31-40, F 41+."""
    rank = cc_rank(value)
    return Rating(CC_LABELS[rank], f"radon {rank}")


def maintainability(value: float) -> Rating:
    """radon's rank of a 0-100 maintainability index (Visual Studio's 20/10 cut points): A above 19,
    B 10-19, C 9 and under."""
    rank = mi_rank(value)
    return Rating(MI_LABELS[rank], f"radon {rank}")


INF = float("inf")

SCALES: dict[str, Callable[[float], Rating]] = {
    # Google Testing Blog 2020: 60 acceptable, 75 commendable, 90 exemplary; SIG 2007's lowest band under 20.
    "tests.coverage.percent": at_least(
        (
            (90, "Excellent", "Google: exemplary"),
            (75, "Good", "Google: commendable"),
            (60, "Fair", "Google: acceptable"),
            (20, "Poor", "under Google's 60"),
            (-INF, "Critical", "SIG: under 20"),
        )
    ),
    "complexity.max": complexity,
    "complexity.mean": complexity,
    "maintainability.weighted_mean": maintainability,
    # SIG 2007 duplication bands: ++ 0-3%, + 3-5%, o 5-10%, - 10-20%, -- 20-100%.
    "duplication.percent": at_most(
        (
            (3, "Excellent", "SIG ++"),
            (5, "Good", "SIG +"),
            (10, "Fair", "SIG o"),
            (20, "Poor", "SIG -"),
            (INF, "Critical", "SIG --"),
        )
    ),
    # Stryker's defaults: high 80, low 60.
    "mutation.score": at_least(
        (
            (80, "Good", "Stryker: high"),
            (60, "Fair", "Stryker: low"),
            (-INF, "Poor", "Stryker: under low"),
        )
    ),
}


def rate(metric: Metric, value: float | None) -> Rating | None:
    """The metric's rating at ``value``; None when it has no published scale, or wasn't measured."""
    scale = SCALES.get(metric.path)
    return None if scale is None or value is None else scale(value)


@dataclass(frozen=True)
class Grade:
    """A target's overall grade, how many ratings it averages, and its weakest rated metric."""

    label: str
    rated: int
    weakest: Metric
    weakest_rating: Rating

    def text(self) -> str:
        """The grade as the report writes it."""
        return (
            f"**Grade: {self.label}**, the mean of {self.rated} rated metrics; "
            f"weakest: {self.weakest.label}, {self.weakest_rating.label} ({self.weakest_rating.band})"
        )


def grade(target: dict) -> Grade | None:
    """SIG's aggregation on one target's measurements: the mean of its ratings, truncated.

    It needs two rated metrics at least: one rating is that metric's, not a grade.
    """
    rated = [(m, r) for m in METRICS if (r := rate(m, m.read(target))) is not None]
    if len(rated) < 2:
        return None
    mean = sum(r.score for _, r in rated) / len(rated)
    weakest, weakest_rating = min(rated, key=lambda mr: mr[1].score)
    return Grade(LABELS[int(mean) - 1], len(rated), weakest, weakest_rating)
