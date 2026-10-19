"""Parsing the tools' output, and the collectors that need no subprocess."""

import json
from pathlib import Path

import pytest

from cq.collectors import complexity, dead_code, maintainability, size, vulnerabilities
from cq.collectors import tests as tests_collector
from cq.collectors.tests import parse_pytest_summary
from cq.config import Settings, Target
from cq.context import Context
from cq.proc import ToolError

SOURCE = '''
"""Module."""


def simple(x):
    return x


def branchy(x):
    if x > 1:
        return 1
    elif x > 2:
        return 2
    for i in range(x):
        if i:
            return i
    return 0


class Thing:
    def method(self):
        return 1
'''


@pytest.fixture
def ctx(tmp_path: Path) -> Context:
    """A fake checkout with one app holding one module and one test."""
    app = tmp_path / "apps" / "demo"
    (app / "pkg").mkdir(parents=True)
    (app / "pkg" / "mod.py").write_text(SOURCE)
    (app / "pkg" / "tests").mkdir()
    (app / "pkg" / "tests" / "test_mod.py").write_text("def test_x():\n    assert True\n")
    target = Target(name="demo", kind="python", root="apps/demo", source=("pkg",), tests=False)
    settings = Settings(
        targets=(target,), jscpd="jscpd@4.0.5", knip="knip@6.41.0", complexity_limit=3, cognitive_limit=15
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    return Context(target=target, checkout=tmp_path, raw_dir=raw, settings=settings)


def test_size_skips_tests(ctx: Context) -> None:
    result = size.collect(ctx)
    assert result["files"] == 1
    assert result["sloc"] > 0


def test_complexity_uses_gate_measurement(ctx: Context) -> None:
    result = complexity.collect(ctx)
    assert result["functions"] == 3
    worst = result["worst"][0]
    assert worst["function"] == "apps/demo/pkg/mod.py::branchy"
    assert worst["cc"] > 3
    assert result["over_limit"] == 1
    assert result["baseline_entries"] == 0


def test_maintainability(ctx: Context) -> None:
    result = maintainability.collect(ctx)
    assert result["files"] == 1
    assert 0 < result["mean"] <= 100


def test_rel_paths(ctx: Context) -> None:
    assert ctx.rel(ctx.app_dir / "pkg" / "mod.py") == "apps/demo/pkg/mod.py"
    assert ctx.rel("pkg/mod.py") == "apps/demo/pkg/mod.py"
    assert ctx.rel("apps/demo/pkg/mod.py", ctx.checkout) == "apps/demo/pkg/mod.py"


def test_vulture_parse(ctx: Context) -> None:
    out = (
        "apps/demo/pkg/mod.py:3: unused import 'os' (90% confidence)\n"
        "apps/demo/pkg/mod.py:9: unused function 'branchy' (60% confidence)\n"
        "apps/demo/pkg/mod.py:12: unreachable code after 'return' (100% confidence)\n"
        "noise\n"
    )
    items = dead_code.parse(out, ctx)
    assert [i["type"] for i in items] == ["import", "function", "unreachable"]
    assert items[0] == {
        "file": "apps/demo/pkg/mod.py",
        "line": 3,
        "type": "import",
        "msg": "unused import 'os'",
        "confidence": 90,
    }


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1590 passed, 29 deselected in 9.40s", {"passed": 1590, "deselected": 29, "failed": 0, "duration_s": 9.4}),
        ("3 failed, 10 passed, 1 error in 2.00s", {"passed": 10, "failed": 3, "errors": 1, "duration_s": 2.0}),
        ("====== 5 passed, 2 skipped in 0.12s ======", {"passed": 5, "skipped": 2}),
    ],
)
def test_pytest_summary(line: str, expected: dict) -> None:
    result = parse_pytest_summary(f"....\n{line}\n")
    assert expected.items() <= result.items()


def test_pytest_summary_missing() -> None:
    with pytest.raises(ToolError):
        parse_pytest_summary("collection error\n")


def test_former_root_used_when_today_root_missing(tmp_path: Path) -> None:
    (tmp_path / "old_name").mkdir()
    target = Target(name="t", kind="python", root="apps/t", source=("x",), tests=False, former_roots=("old_name",))
    assert target.root_in(tmp_path) == "old_name"
    (tmp_path / "apps" / "t").mkdir(parents=True)
    assert target.root_in(tmp_path) == "apps/t"


def test_vitest_coverage_and_floor(ctx: Context) -> None:
    (ctx.app_dir / "vitest.config.mts").write_text(
        "export default { test: { coverage: { thresholds: { statements: 35.7, branches: 26.45, lines: 35.05 } } } };"
    )
    pct = {"total": 10, "covered": 4, "skipped": 0, "pct": 40}
    summary = {
        "total": {"lines": pct, "statements": pct, "functions": pct, "branches": {"total": 8, "covered": 2, "pct": 25}},
        str(ctx.app_dir / "src" / "a.ts"): {"lines": {"total": 10, "covered": 4, "pct": 40}},
        str(ctx.app_dir / "src" / "b.ts"): {"lines": {"total": 5, "covered": 5, "pct": 100}},
    }
    cov = tests_collector.vitest_coverage(summary, ctx)
    assert (cov["percent"], cov["branch_percent"], cov["missing_branches"]) == (40.0, 25.0, 6)
    assert (cov["fail_under"], cov["branch_floor"]) == (35.05, 26.45)
    assert cov["worst_files"] == [{"file": "apps/demo/src/a.ts", "percent": 40.0, "missing": 6}]


@pytest.mark.parametrize(("fix", "label"), [({"version": "2.0.1"}, "2.0.1"), ({}, "yes"), (True, "yes"), (False, "no")])
def test_npm_audit_fix_label(fix: object, label: str) -> None:
    assert vulnerabilities.fix_label(fix) == label


def test_pip_audit_on_the_locked_requirements(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    audit = {
        "dependencies": [
            {"name": "requests", "version": "2.0.0", "vulns": []},
            {
                "name": "mcp",
                "version": "1.29.0",
                "vulns": [
                    {"id": "CVE-2026-59951", "fix_versions": ["2.2.0", "1.30.0"]},
                    {"id": "GHSA-xxxx", "fix_versions": ["1.30.0"]},
                ],
            },
            {"name": "abandoned", "version": "0.1", "vulns": [{"id": "PYSEC-1"}]},
        ]
    }
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path, ok_codes: tuple[int, ...] = (0,)) -> str:
        commands.append(cmd)
        if cmd[0] == "uv":
            return "mcp==1.29.0\nrequests==2.0.0\n"
        Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(audit))
        return ""

    monkeypatch.setattr(vulnerabilities, "run", fake_run)
    (ctx.app_dir / "uv.lock").write_text("")
    result = vulnerabilities.collect(ctx)
    assert (result["packages"], result["vulnerable_packages"], result["vulnerabilities"]) == (3, 2, 3)
    assert result["items"] == [
        {"package": "mcp", "version": "1.29.0", "ids": "CVE-2026-59951, GHSA-xxxx", "fix": "1.30.0, 2.2.0"},
        {"package": "abandoned", "version": "0.1", "ids": "PYSEC-1", "fix": "none yet"},
    ]
    assert "--frozen" in commands[0]  # the lock as it is, never re-resolved
    assert "--no-deps" in commands[1]


def test_pip_audit_needs_a_lock(ctx: Context) -> None:
    with pytest.raises(ToolError, match=r"no uv\.lock"):
        vulnerabilities.collect(ctx)
