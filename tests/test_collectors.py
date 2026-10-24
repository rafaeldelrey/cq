"""Parsing the tools' output, and the collectors that need no subprocess."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cq import findings, requirements, runner
from cq.collectors import complexity, dead_code, freshness, maintainability, size, vulnerabilities
from cq.collectors import tests as tests_collector
from cq.collectors.tests import parse_pytest_summary
from cq.config import Settings, Target
from cq.context import Context
from cq.proc import ToolError
from cq.requirements import SKIP_NO_MANIFEST

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
    (ctx.app_dir / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\n")
    (ctx.app_dir / "uv.lock").write_text("")
    result = vulnerabilities.collect(ctx)
    assert (result["packages"], result["vulnerable_packages"], result["vulnerabilities"]) == (3, 2, 3)
    assert result["items"] == [
        {"package": "mcp", "version": "1.29.0", "ids": "CVE-2026-59951, GHSA-xxxx", "fix": "1.30.0, 2.2.0"},
        {"package": "abandoned", "version": "0.1", "ids": "PYSEC-1", "fix": "none yet"},
    ]
    assert "--frozen" in commands[0]  # the lock as it is, never re-resolved
    assert "--no-deps" in commands[1]


def test_repeated_pip_audit_advisories_count_once(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """pip-audit 2.10 lists some advisories twice (#69): the same ID, or a PYSEC ID and the GHSA it aliases."""
    first = {"id": "PYSEC-2026-1", "aliases": ["GHSA-aaaa", "CVE-2026-1"], "fix_versions": ["2.0"], "description": "a"}
    other = {"id": "PYSEC-2026-2", "aliases": ["GHSA-bbbb"], "fix_versions": ["3.0"], "description": "b"}
    audit = {
        "dependencies": [
            {
                "name": "demo-http",
                "version": "1.0",
                "vulns": [
                    first,
                    other,
                    first,  # an identical copy
                    {**other, "description": "b, reworded"},  # a copy with another description
                    {"id": "GHSA-aaaa", "aliases": ["CVE-2026-1"], "fix_versions": ["2.0"]},  # first, by its alias
                ],
            },
            {"name": "demo-yaml", "version": "0.1", "vulns": [{"id": "PYSEC-2026-3"}, {"id": "PYSEC-2026-4"}]},
        ]
    }

    def fake_run(cmd: list[str], cwd: Path, ok_codes: tuple[int, ...] = (0,)) -> str:
        if cmd[0] == "uv":
            return "demo-http==1.0\ndemo-yaml==0.1\n"
        Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(audit))
        return ""

    monkeypatch.setattr(vulnerabilities, "run", fake_run)
    (ctx.app_dir / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\n")
    (ctx.app_dir / "uv.lock").write_text("")
    result = vulnerabilities.collect(ctx)
    assert (result["packages"], result["vulnerable_packages"], result["vulnerabilities"]) == (2, 2, 4)
    assert [(i["package"], i["ids"], i["fix"]) for i in result["items"]] == [
        ("demo-http", "PYSEC-2026-1, PYSEC-2026-2", "2.0, 3.0"),
        ("demo-yaml", "PYSEC-2026-3, PYSEC-2026-4", "none yet"),  # distinct advisories stay
    ]


def test_pip_audit_without_a_lock_audits_a_resolution_in_the_run_folder(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unlocked uv app is resolved by uv pip compile in its own folder; only `==` lines are audited (#60)."""
    (ctx.app_dir / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n\n[dependency-groups]\ndev = ['pytest']\n"
    )
    commands: list[tuple[list[str], Path]] = []
    audited: list[str] = []

    def fake_run(cmd: list[str], cwd: Path, ok_codes: tuple[int, ...] = (0,)) -> str:
        """uv writes its resolution, pip-audit its report; both print nothing on stdout."""
        commands.append((cmd, cwd))
        out = Path(cmd[cmd.index("-o") + 1])
        if cmd[0] == "uv":
            out.write_text(
                "../libs/helper\nhelper @ git+https://example.com/helper.git\nsix==1.17.0\nrequests[socks]==2.32.4\n"
            )
        else:
            audited.extend(Path(cmd[cmd.index("-r") + 1]).read_text().splitlines())
            out.write_text(json.dumps({"dependencies": []}))
        return ""

    monkeypatch.setattr(vulnerabilities, "run", fake_run)
    result = vulnerabilities.collect(ctx)
    assert (result["packages"], result["unaudited"]) == (0, 2)  # the path and the VCS line, counted
    compile_cmd, cwd = commands[0]
    assert compile_cmd[:3] == ["uv", "pip", "compile"] and cwd == ctx.app_dir  # uv applies the app's own settings
    assert "--group" in compile_cmd and Path(compile_cmd[compile_cmd.index("-o") + 1]).parent == ctx.raw_dir
    assert audited == ["six==1.17.0", "requests[socks]==2.32.4"]  # no path or VCS line
    assert not (ctx.app_dir / "uv.lock").exists()


def test_pip_audit_without_any_manifest_is_skipped_with_the_reason(ctx: Context) -> None:
    assert vulnerabilities.collect(ctx) == {"skipped": SKIP_NO_MANIFEST}


def test_hashed_pins_are_kept_whole(tmp_path: Path) -> None:
    """uv's generate-hashes continues each pin over `\\` lines: the requirement is kept, not its first line."""
    req = tmp_path / "requirements.txt"
    req.write_text(
        "attrs==26.1.0 \\\n    --hash=sha256:aaaa \\\n    --hash=sha256:bbbb\n"
        "six==1.17.0 \\\n    --hash=sha256:cccc\n"
        "../libs/helper\n"
    )
    assert vulnerabilities.keep_pinned(req) == 1
    assert req.read_text().splitlines() == [
        "attrs==26.1.0      --hash=sha256:aaaa      --hash=sha256:bbbb",
        "six==1.17.0      --hash=sha256:cccc",
    ]


def test_a_resolution_that_needs_a_build_is_skipped_with_the_reason(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PyPI package with only a source distribution can't resolve without a build: cq runs none (#60)."""
    (ctx.app_dir / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\ndependencies = ['old']\n")

    def refused(cmd: list[str], cwd: Path, **_: object) -> str:
        assert "--no-build" in cmd
        raise ToolError(
            "uv pip compile exited 1",
            returncode=1,
            stderr="hint: Wheels are required for `old` because building from source is disabled for all packages",
        )

    monkeypatch.setattr(vulnerabilities, "run", refused)
    assert vulnerabilities.collect(ctx) == {"skipped": requirements.SKIP_NEEDS_BUILD}


def test_a_failed_resolution_other_than_a_refused_build_is_a_tool_error(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    (ctx.app_dir / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\ndependencies = ['helper']\n\n"
        "[tool.uv.sources]\nhelper = { path = '../helper' }\n"
    )

    def offline(cmd: list[str], cwd: Path, **_: object) -> str:
        raise ToolError("uv pip compile exited 2", returncode=2, stderr="error: failed to fetch index")

    monkeypatch.setattr(vulnerabilities, "run", offline)
    with pytest.raises(ToolError, match="exited 2"):
        vulnerabilities.collect(ctx)


def test_an_unlocked_app_with_dynamic_dependencies_is_skipped_and_nothing_built(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving dynamic metadata builds the project, which writes <name>.egg-info into the repo (#60)."""
    (ctx.app_dir / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\ndynamic = ['dependencies']\n"
    )

    def no_run(cmd: list[str], cwd: Path, **_: object) -> str:
        raise AssertionError(f"ran {cmd}")

    monkeypatch.setattr(vulnerabilities, "run", no_run)
    monkeypatch.setattr(freshness, "run", no_run)
    assert vulnerabilities.collect(ctx) == {"skipped": requirements.SKIP_DYNAMIC}
    assert freshness.collect(ctx) == {"skipped": requirements.SKIP_DYNAMIC}


def test_a_dependency_taken_from_a_local_source_is_not_a_pypi_pin(tmp_path: Path) -> None:
    """Path, git, URL and workspace sources aren't PyPI's; one whose marker doesn't hold here still is."""
    here, elsewhere = sys.platform, "no-such-platform"
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n"
        "dependencies = ['Helper_Lib', 'tool', 'other==2.0', 'mirror==1.0', 'member', 'six>=1']\n\n"
        "[tool.uv.sources]\n"
        "helper-lib = { path = '../libs/helper' }\n"
        f"tool = [{{ git = 'https://example.com/tool.git', marker = \"sys_platform == '{here}'\" }}]\n"
        f"other = [{{ git = 'https://example.com/other.git', marker = \"sys_platform == '{elsewhere}'\" }}]\n"
        "mirror = { index = 'internal' }\n"
        "member = { workspace = true }\n"
    )
    assert requirements.project_requirements(tmp_path) == [
        requirements.Requirement("other", "2.0"),  # its git source is for another platform: PyPI's here
        requirements.Requirement("mirror", "1.0"),  # an index is still a registry: compared
        requirements.Requirement("six", None),
    ]


def busy_module(n: int) -> str:
    """``n`` functions of CC 5 (four branches each): over the test context's CC limit of 3."""
    return "".join(
        f"def f{i}(x):\n    if x > 1:\n        return 1\n    if x > 2:\n        return 2\n"
        f"    if x > 3:\n        return 3\n    if x > 4:\n        return 4\n    return 0\n\n\n"
        for i in range(n)
    )


def test_complexity_keeps_every_function_over_the_limit(ctx: Context) -> None:
    (ctx.app_dir / "pkg" / "busy.py").write_text(busy_module(20))
    result, error = runner.run_collector(ctx, "complexity")
    assert error is None and result is not None
    assert len(result["worst"]) == 15
    assert result["truncated"] == {"worst": result["functions"]}
    assert len(result["over_limit_items"]) == result["over_limit"] >= 20
    over = [f for f in findings.extract("demo", "complexity", result) if f["rule"] == "cc-over-limit"]
    assert len(over) == result["over_limit"]  # one finding each, not the top 15


def test_a_snapshot_without_over_limit_items_keeps_its_old_findings() -> None:
    old = {
        "limit": 15,
        "worst": [{"function": "a.py::f", "cc": 20, "rank": "D"}, {"function": "a.py::g", "cc": 3, "rank": "A"}],
    }
    assert [f["symbol"] for f in findings.extract("app", "complexity", old)] == ["f"]


def test_a_list_within_its_cap_is_not_marked(ctx: Context) -> None:
    result, _ = runner.run_collector(ctx, "complexity")
    assert result is not None
    assert "truncated" not in result


def test_the_gate_keeps_every_item_and_marks_no_cut(ctx: Context) -> None:
    (ctx.app_dir / "pkg" / "busy.py").write_text(busy_module(20))
    full = replace(ctx, full=True)
    result, _ = runner.run_collector(full, "complexity")
    assert result is not None
    assert "truncated" not in result
    assert len(result["worst"]) == result["functions"]


def test_cuts_belong_to_the_collector_that_made_them(ctx: Context) -> None:
    (ctx.app_dir / "pkg" / "busy.py").write_text(busy_module(20))
    first, _ = runner.run_collector(ctx, "complexity")
    second, _ = runner.run_collector(ctx, "size")  # same context, next collector: nothing carried over
    assert first is not None and second is not None
    assert "truncated" in first and "truncated" not in second


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 0, 1), (1, 2, 0)])
def test_advisories_linked_through_aliases_are_one_whatever_the_order(order: tuple[int, int, int]) -> None:
    """A and B share nothing, C aliases both: one advisory, with every fix version, in any order."""
    entries = [
        {"id": "GHSA-aaaa", "fix_versions": ["1.1"]},
        {"id": "CVE-2026-9", "fix_versions": ["1.2"]},
        {"id": "PYSEC-2026-9", "aliases": ["GHSA-aaaa", "CVE-2026-9"], "fix_versions": []},
    ]
    (merged,) = vulnerabilities.distinct_advisories([entries[i] for i in order])
    assert merged["id"] == entries[order[0]]["id"]
    assert merged["fix_versions"] == ["1.1", "1.2"]


def test_a_later_pypi_id_aliasing_an_earlier_github_one_is_the_same_advisory() -> None:
    entries = [
        {"id": "GHSA-bbbb", "aliases": ["CVE-2026-8"], "fix_versions": ["2.0"]},
        {"id": "PYSEC-2026-8", "aliases": ["GHSA-bbbb"], "fix_versions": ["2.1"]},
        {"id": "PYSEC-2026-7", "fix_versions": []},
    ]
    assert [(v["id"], v["fix_versions"]) for v in vulnerabilities.distinct_advisories(entries)] == [
        ("GHSA-bbbb", ["2.0", "2.1"]),
        ("PYSEC-2026-7", []),
    ]
