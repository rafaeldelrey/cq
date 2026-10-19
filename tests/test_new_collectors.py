"""The collectors added on 2026-10-14: hotspots, suppressions, the mock-only test rule, containers,
freshness, scripts and links. Binaries run for real on small inputs; a missing one skips its test."""

import json
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cq import findings, runner
from cq.collectors import containers, dependencies, freshness, hotspots, links, scripts, semgrep, suppressions
from cq.config import DEFAULTS_DIR, Settings, Target
from cq.context import Context
from cq.proc import own

BRANCHY = """
def branchy(x):
    if x > 1:
        return 1
    elif x > 2:
        return 2
    for i in range(x):
        if i:
            return i
    return 0
"""


def git(cwd: Path, *args: str) -> None:
    """Run git quietly in a test repo."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_ctx(root: Path, kind: str = "python", **settings: object) -> Context:
    """A context over an app at apps/demo with production code in pkg/."""
    target = Target(name="demo", kind=kind, root="apps/demo", source=("pkg",), tests=False)
    base = {"jscpd": "j", "knip": "k", "complexity_limit": 3, "cognitive_limit": 15}
    raw = root / "raw"
    raw.mkdir(exist_ok=True)
    return Context(target=target, checkout=root, raw_dir=raw, settings=Settings(targets=(target,), **(base | settings)))  # type: ignore[arg-type]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with apps/demo/pkg/mod.py committed six times."""
    pkg = tmp_path / "apps" / "demo" / "pkg"
    pkg.mkdir(parents=True)
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    for n in range(6):
        (pkg / "mod.py").write_text(BRANCHY + f"\n# change {n}\n")
        (pkg / "calm.py").write_text("def calm():\n    return 1\n") if n == 0 else None
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", f"c{n}")
    return tmp_path


def test_hotspots_rank_churn_times_complexity(repo: Path) -> None:
    result = hotspots.collect(make_ctx(repo))
    top = result["items"][0]
    assert top["file"] == "apps/demo/pkg/mod.py"
    assert top["commits"] == 6
    assert top["score"] == 6 * top["complexity"]
    assert result["hotspots"] == 1  # mod.py: ≥5 commits and a function over CC 3; calm.py has 1 commit
    assert [i["file"] for i in result["items"]][-1] == "apps/demo/pkg/calm.py"


def test_suppressions_count_and_reasons(tmp_path: Path) -> None:
    pkg = tmp_path / "apps" / "demo" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text(
        "x = 1  # noqa: BLE001 - observability-only, re-raised\ny = 2  # type: ignore\nz = 3  # pragma: no cover\n"
    )
    result = suppressions.collect(make_ctx(tmp_path))
    assert result["total"] == 3
    assert result["by_kind"] == {"noqa": 1, "type-ignore": 1, "no-cover": 1}
    assert result["without_reason"] == 2
    assert result["items"][0]["reason"] is False  # the ones without a reason come first


@pytest.mark.skipif(shutil.which(own("semgrep")) is None, reason="semgrep not installed")
def test_mock_only_rule_finds_tests_under_a_tests_folder(tmp_path: Path) -> None:
    tests = tmp_path / "apps" / "demo" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text(
        "from unittest.mock import MagicMock\n\n"
        "def test_only_mock():\n    m = MagicMock()\n    m.send('x')\n    m.send.assert_called_once_with('x')\n\n"
        "def test_not_called_is_fine():\n    m = MagicMock()\n    m.send.assert_not_called()\n\n"
        "def test_with_assert():\n    m = MagicMock()\n    m.send('x')\n    m.send.assert_called_once()\n    assert m.send.call_count == 1\n"
    )
    rules = semgrep.rule_files(DEFAULTS_DIR / "semgrep" / "tests")
    found = semgrep.scan_tests(make_ctx(tmp_path), tests, rules)
    hits = [f for f in found if f.get("code") == "cq-test-only-asserts-mocks"]
    assert [(h["file"], h["line"]) for h in hits] == [("apps/demo/tests/test_x.py", 3)]


@pytest.mark.skipif(shutil.which("hadolint") is None, reason="hadolint not installed")
def test_containers_hadolint_and_no_image(tmp_path: Path) -> None:
    app = tmp_path / "apps" / "demo"
    (app / "pkg").mkdir(parents=True)
    (app / "Dockerfile").write_text("FROM python:3.12\nRUN pip install requests\n")
    ctx = make_ctx(tmp_path)
    assert containers.trivy(ctx) == {"skipped": "no image configured"}
    lint = containers.hadolint(ctx)
    assert "DL3013" in {i["code"] for i in lint["items"]}
    assert lint["items"][0]["file"] == "apps/demo/Dockerfile"


def test_freshness_parsing_and_libyear() -> None:
    out = (
        "agents-server v0.1.0\n"
        "├── fastapi v0.141.1 (latest: v0.143.0)\n"
        "├── httpx v0.28.1\n"
        "└── pytest v8.4.1 (group: dev) (latest: v9.0.0)\n"
    )
    found = [(m["name"], m["current"], m["latest"]) for m in freshness.UV_OUTDATED.finditer(out)]
    assert found == [("fastapi", "0.141.1", "0.143.0"), ("pytest", "8.4.1", "9.0.0")]
    assert freshness.major("v9.0.0") == 9
    dates = {"1.0": datetime(2025, 1, 1, tzinfo=UTC), "2.0": datetime(2026, 1, 1, tzinfo=UTC)}
    assert round(freshness.lag_years(dates, "1.0", "2.0"), 2) == 1.0
    assert freshness.lag_years(dates, "1.0", "9.9") == 0.0
    summary = freshness.summarise(
        [{"package": "a", "major": True, "years": 0.5}, {"package": "b", "major": False, "years": 1.25}]
    )
    assert (summary["outdated"], summary["major_behind"], summary["libyear"]) == (2, 1, 1.75)
    assert summary["items"][0]["package"] == "b"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_scripts_finds_tracked_shell_scripts_at_any_depth(tmp_path: Path) -> None:
    (tmp_path / "scripts" / "deep").mkdir(parents=True)
    (tmp_path / "scripts" / "top.sh").write_text("#!/bin/bash\necho $1\n")
    (tmp_path / "scripts" / "deep" / "low.sh").write_text('#!/bin/bash\nfor f in $(ls); do echo "$f"; done\n')
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    ctx = make_ctx(tmp_path, kind="repo", shell_roots=("scripts",))
    assert scripts.tracked_scripts(ctx) == ["scripts/deep/low.sh", "scripts/top.sh"]
    result = scripts.shellcheck(ctx)
    assert result["files"] == 2
    assert {i["code"] for i in result["items"]} >= {"SC2086"}  # the unquoted $1


@pytest.mark.skipif(shutil.which("lychee") is None, reason="lychee not installed")
def test_links_reports_a_broken_relative_link(tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "ok.md").write_text("# ok\n")
    (tmp_path / "wiki" / "index.md").write_text("[fine](ok.md) and [gone](missing.md)\n")
    result = links.collect(make_ctx(tmp_path, kind="repo", link_paths=("wiki",)))
    assert result["broken"] == 1
    assert result["items"][0]["file"] == "wiki/index.md"
    assert result["items"][0]["target"].endswith("wiki/missing.md")


def test_new_tools_become_findings() -> None:
    snap = {
        "errors": {},
        "targets": {
            "app": {
                "hotspots": {
                    "window_days": 90,
                    "items": [{"file": "a.py", "commits": 6, "complexity": 40, "worst_cc": 20, "score": 240}],
                },
                "suppressions": {
                    "items": [{"file": "a.py", "line": 3, "kind": "noqa", "reason": False, "text": "# noqa"}]
                },
                "containers": {
                    "dockerfile": {
                        "items": [{"file": "Dockerfile", "line": 2, "code": "DL3013", "level": "warning", "msg": "pin"}]
                    },
                    "image": {
                        "image": "img:latest",
                        "items": [
                            {
                                "package": "openssl",
                                "version": "3",
                                "id": "CVE-1",
                                "severity": "high",
                                "fix": "3.1",
                                "title": "t",
                            }
                        ],
                    },
                },
                "freshness": {
                    "items": [{"package": "next", "current": "15.0", "latest": "16.0", "major": True, "years": 0.8}]
                },
            },
            "repo": {
                "scripts": {
                    "shell": {
                        "items": [{"file": "s.sh", "line": 2, "code": "SC2086", "level": "info", "msg": "quote"}]
                    },
                    "workflows": {"items": []},
                },
                "links": {"items": [{"file": "wiki/a.md", "target": "wiki/b.md", "status": ""}]},
            },
        },
    }
    got = {(f["tool"], f["rule"], f["severity"]) for f in findings.from_snapshot(snap)}
    assert got == {
        ("hotspots", "hotspot", "warning"),
        ("suppressions", "noqa", "warning"),
        ("containers", "hadolint-DL3013", "warning"),
        ("containers", "image-high", "error"),  # fixable
        ("freshness", "major-behind", "warning"),
        ("scripts", "SC2086", "info"),
        ("links", "broken-link", "warning"),
    }


def test_house_rule_ids_match_findings_by_their_last_segment(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "rules.yml").write_text("rules:\n  - id: company.security.no-eval\n  - id: plain-rule\n")
    (tmp_path / "tests" / "t.yml").write_text("rules:\n  - id: company.tests.mock-only\n")
    assert semgrep.house_rule_ids(tmp_path) == {"no-eval", "plain-rule", "mock-only"}


def fake_tool_output(monkeypatch: pytest.MonkeyPatch, module: object, data: dict) -> None:
    """The module's ``run`` writes ``data`` where ``--output`` points, and answers git with a fresh commit date."""

    def fake_run(cmd: list[str], cwd: Path, ok_codes: tuple[int, ...] = (0,)) -> str:
        if cmd[0] == "git":
            return datetime.now(UTC).isoformat()
        Path(cmd[cmd.index("--output") + 1]).write_text(json.dumps(data))
        return ""

    monkeypatch.setattr(module, "run", fake_run)


TRIVY_VULN = {"PkgName": "openssl", "VulnerabilityID": "CVE-1", "Severity": "HIGH", "FixedVersion": "3.0.1"}


def image_ctx(tmp_path: Path) -> Context:
    """A context whose target has a published image."""
    ctx = make_ctx(tmp_path)
    return replace(ctx, target=replace(ctx.target, image="ghcr.io/acme/demo:latest"))


def test_trivy_reads_the_image_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = {"OS": {"Family": "debian", "Name": "12.5"}, "RepoDigests": ["ghcr.io/acme/demo@sha256:abc"]}
    fake_tool_output(monkeypatch, containers, {"Metadata": meta, "Results": [{"Vulnerabilities": [TRIVY_VULN]}]})
    result = containers.trivy(image_ctx(tmp_path))
    assert (result["os"], result["digest"], result["high"], result["fixable_high"]) == (
        "debian 12.5",
        "ghcr.io/acme/demo@sha256:abc",
        1,
        1,
    )


def test_trivy_without_os_or_digest_still_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tool_output(monkeypatch, containers, {"Metadata": {}, "Results": [{"Vulnerabilities": [TRIVY_VULN]}]})
    result = containers.trivy(image_ctx(tmp_path))  # a scratch image, built locally: no OS, no digest
    assert (result["os"], result["digest"], result["high"]) == ("", "", 1)


def test_trivy_output_without_metadata_is_an_error_not_a_clean_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tool_output(monkeypatch, containers, {"Results": []})
    with pytest.raises(KeyError, match="Metadata"):
        containers.trivy(image_ctx(tmp_path))


def test_links_reads_lychee_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = f"file://{tmp_path.resolve()}/"
    errors = {"wiki/index.md": [{"url": f"{root}wiki/missing.md", "status": {"text": "Cannot find file"}}]}
    fake_tool_output(monkeypatch, links, {"total": 2, "error_map": errors})
    monkeypatch.setattr(links.shutil, "which", lambda name: f"/bin/{name}")
    (tmp_path / "wiki").mkdir()
    result = links.collect(make_ctx(tmp_path, kind="repo", link_paths=("wiki",)))
    assert result["items"] == [{"file": "wiki/index.md", "target": "wiki/missing.md", "status": "Cannot find file"}]


def test_lychee_output_without_its_error_map_is_an_error_not_zero_broken_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tool_output(monkeypatch, links, {"total": 2})
    monkeypatch.setattr(links.shutil, "which", lambda name: f"/bin/{name}")
    (tmp_path / "wiki").mkdir()
    with pytest.raises(KeyError, match="error_map"):
        links.collect(make_ctx(tmp_path, kind="repo", link_paths=("wiki",)))


def test_a_missing_required_key_lands_in_the_snapshot_as_a_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tool_output(monkeypatch, links, {"total": 2})
    monkeypatch.setattr(links.shutil, "which", lambda name: f"/bin/{name}")
    (tmp_path / "wiki").mkdir()
    result, error = runner.run_collector(make_ctx(tmp_path, kind="repo", link_paths=("wiki",)), "links")
    assert result is None
    assert error == "KeyError: 'error_map'"


def test_high_items_keep_each_critical_or_high_advisory_once() -> None:
    medium = {"PkgName": "zlib", "VulnerabilityID": "CVE-2", "Severity": "MEDIUM"}
    critical = {"PkgName": "glibc", "VulnerabilityID": "CVE-3", "Severity": "CRITICAL"}
    items = containers.high_items([TRIVY_VULN, medium, critical, TRIVY_VULN])
    assert [(i["package"], i["severity"], i["fix"]) for i in items] == [
        ("glibc", "critical", "none yet"),
        ("openssl", "high", "3.0.1"),
    ]


def test_knip_issues_by_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    knip = {
        "files": ["src/old.ts"],
        "issues": [
            {
                "file": "src/a.ts",
                "exports": [{"name": "unusedFn", "line": 3}],
                "dependencies": ["left-pad"],
                "types": [],
                "owners": "not a list",
            }
        ],
    }
    monkeypatch.setattr(dependencies, "run", lambda cmd, cwd, ok_codes: json.dumps(knip))
    result = dependencies.collect_typescript(make_ctx(tmp_path, kind="typescript"))
    assert result["by_kind"] == {"exports": 1, "dependencies": 1, "files": 1}
    assert result["items"] == [
        {"file": "apps/demo/src/a.ts", "kind": "exports", "name": "unusedFn", "line": 3},
        {"file": "apps/demo/src/a.ts", "kind": "dependencies", "name": "left-pad", "line": None},
        {"file": "apps/demo/src/old.ts", "kind": "files", "name": "unused file", "line": None},
    ]


def test_a_folder_of_packages_names_each_package(tmp_path: Path) -> None:
    (tmp_path / "apps" / "core" / "billing").mkdir(parents=True)
    (tmp_path / "apps" / "core" / "billing" / "__init__.py").write_text("")
    (tmp_path / "apps" / "core" / "notes.txt").write_text("")
    assert dependencies.package_names(tmp_path / "apps" / "core") == ["billing"]
