"""Reading metrics out of snapshots, deltas, and the rendered reports."""

import json
from pathlib import Path

from cq import dashboard, render_md
from cq.metrics import METRICS, Metric, load_history, rows_for, verdict


def snap(day: str, sha: str, coverage: float, over: int, dirty: bool = False) -> dict:
    """A minimal snapshot with two measured metrics."""
    return {
        "schema": 1,
        "run_id": f"x_{sha}",
        "run_at": f"{day}T12:00:00+00:00",
        "commit": sha * 6,
        "short": sha,
        "commit_date": f"{day}T10:00:00+00:00",
        "subject": "subject",
        "dirty": dirty,
        "partial": False,
        "tool_versions": {},
        "errors": {},
        "targets": {
            "app": {
                "tests": {"coverage": {"percent": coverage, "worst_files": []}, "results": {"passed": 10, "failed": 0}},
                "complexity": {
                    "over_limit": over,
                    "limit": 15,
                    "worst": [{"function": "a.py::f", "cc": 20, "rank": "C"}],
                },
            }
        },
    }


def write(tmp_path: Path, *snaps: dict) -> list[dict]:
    """Store snapshots in a history dir and load them back."""
    for s in snaps:
        (tmp_path / f"{s['commit_date'][:10]}_{s['short']}.json").write_text(json.dumps(s))
    return load_history(tmp_path)


def metric(path: str) -> Metric:
    """A metric from the registry by path."""
    return next(m for m in METRICS if m.path == path)


def test_read_nested_and_missing() -> None:
    m = metric("tests.coverage.percent")
    assert m.read({"tests": {"coverage": {"percent": 88.5}}}) == 88.5
    assert m.read({"tests": {"skipped": "no tests"}}) is None
    assert m.read({}) is None


def test_verdict_follows_direction() -> None:
    assert verdict(metric("tests.coverage.percent"), 1.0) == "better"
    assert verdict(metric("tests.coverage.percent"), -1.0) == "worse"
    assert verdict(metric("complexity.over_limit"), -2) == "better"
    assert verdict(metric("complexity.over_limit"), 0) == "same"
    assert verdict(metric("size.sloc"), 500) == ""
    assert verdict(metric("size.sloc"), None) == ""


def test_history_sorted_by_commit_date(tmp_path: Path) -> None:
    history = write(tmp_path, snap("2026-10-01", "bbb", 90, 3), snap("2026-08-01", "aaa", 85, 5))
    assert [s["short"] for s in history] == ["aaa", "bbb"]


def test_rows_deltas(tmp_path: Path) -> None:
    history = write(
        tmp_path,
        snap("2026-07-01", "aaa", 80, 6),
        snap("2026-09-20", "bbb", 85, 5),
        snap("2026-10-10", "ccc", 84, 3),
    )
    rows = {r.metric.path: r for r in rows_for(history, "app", window_days=30)}
    cov = rows["tests.coverage.percent"]
    assert cov.now == 84
    assert cov.prev == 85
    assert cov.window == 80  # the last snapshot at or before 2026-09-10
    assert cov.first == 80
    assert [v for _, v in cov.points] == [80, 85, 84]
    assert "size.sloc" not in rows  # not measured: no row


def test_single_snapshot_has_no_deltas(tmp_path: Path) -> None:
    history = write(tmp_path, snap("2026-10-10", "aaa", 84, 3))
    row = rows_for(history, "app", 30)[0]
    assert (row.prev, row.window, row.first) == (None, None, None)


def test_markdown_report(tmp_path: Path) -> None:
    history = write(tmp_path, snap("2026-09-01", "aaa", 85, 5), snap("2026-10-10", "bbb", 84, 3))
    text = render_md.render(history)
    assert "# Code quality: 2026-10-10 (bbb)" in text
    assert "🔻 **app** Coverage: 85.00% → 84.00%" in text
    assert "✅ **app** Functions over CC limit: 5 → 3" in text
    assert "`a.py::f` CC 20 (C)" in text


def test_sparkline() -> None:
    assert render_md.sparkline([1]) == ""
    assert render_md.sparkline([1, 2, 3]) == "▁▅█"
    assert render_md.sparkline([2, 2]) == "▄▄"


def test_dashboard(tmp_path: Path) -> None:
    history = write(tmp_path, snap("2026-09-01", "aaa", 85, 5), snap("2026-10-10", "bbb", 84, 3, dirty=True))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "app").mkdir()
    (run_dir / "app" / "ruff.json").write_text("[]")
    page = dashboard.build(history, history[-1], run_dir, "## Verdict\n- **Worse:** coverage `-1%`\n</script>")
    assert "<title>HanBot Code Quality</title>" in page[:300]
    assert "/*__DATA__*/" not in page
    payload = page.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
    data = json.loads(payload)
    assert [p["short"] for p in data["series"]] == ["aaa", "bbb"]
    assert data["series"][1]["values"]["app"]["tests.coverage.percent"] == 84
    assert data["files"] == [{"path": "app/ruff.json", "bytes": 2}]
    assert "<strong>Worse:</strong>" in data["analysis"]
    assert "&lt;/script&gt;" in data["analysis"]


def test_history_until_puts_run_last_and_drops_later_commits(tmp_path: Path) -> None:
    history = write(tmp_path, snap("2026-09-01", "aaa", 85, 5), snap("2026-10-10", "bbb", 84, 3))
    run = snap("2026-09-15", "ccc", 86, 4)
    assert [s["short"] for s in dashboard.history_until(history, run)] == ["aaa", "ccc"]


def test_markdown_to_html() -> None:
    out = dashboard.markdown_to_html(
        "# Title\n\nSome **bold** text\ncontinued.\n\n1. one\n2. two\n- a [link](https://x.y)"
    )
    assert "<h3>Title</h3>" in out
    assert "<p>Some <strong>bold</strong> text continued.</p>" in out
    assert "<ol>\n<li>one</li>\n<li>two</li>\n</ol>\n<ul>" in out
    assert '<a href="https://x.y"' in out


def test_new_findings_named(tmp_path: Path) -> None:
    old = snap("2026-09-01", "aaa", 85, 1)
    new = snap("2026-10-10", "bbb", 85, 2)
    new["targets"]["app"]["complexity"]["worst"].append({"function": "b.py::g", "cc": 18, "rank": "C"})
    text = render_md.render(write(tmp_path, old, new))
    section = text.split("**New since the previous snapshot**")[1].split("\n\n")[0]
    assert "`b.py` `g` cyclomatic complexity 18 > 15" in section
    assert "a.py" not in section
