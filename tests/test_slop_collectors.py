"""The #284 collectors: radarlint and smells (qlty), aislop, pyscn, fallow, vale, cspell, and the
betterleaks + trufflehog secrets scan.

Cheap binaries run for real on small inputs (a missing one skips its test). qlty, aislop, pyscn,
fallow and cspell are fed recorded output shaped like what they print on this repo, so the test
checks the parsing and filtering, not the tool.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cq import findings, proc, qlty
from cq.collectors import aislop, cspell, fallow, pyscn, radarlint, secrets, smells, vale
from cq.config import DEFAULTS_DIR, PINS, Settings, Target, load_settings
from cq.context import Context
from cq.proc import ToolError, log_to, run


def make_ctx(root: Path, kind: str = "python", **settings: object) -> Context:
    """A context over an app at apps/demo with production code in pkg/."""
    target = Target(name="demo", kind=kind, root="apps/demo", source=("pkg",), tests=False)
    (root / "apps" / "demo" / "pkg").mkdir(parents=True, exist_ok=True)
    base = {
        "jscpd": "j", "knip": "k", "complexity_limit": 15, "cognitive_limit": 15, "qlty": "0.654.0",
        "aislop": "aislop@0", "pyscn": "pyscn==0", "fallow": "fallow@0", "cspell": "cspell@0",
    }  # fmt: skip
    raw = root / "raw"
    raw.mkdir(exist_ok=True)
    return Context(target=target, checkout=root, raw_dir=raw, settings=Settings(targets=(target,), **(base | settings)))  # type: ignore[arg-type]


def sarif(*results: tuple[str, str, str, int, str]) -> dict:
    """A qlty-shaped SARIF document: (ruleId, level, file, line, message) per result."""
    return {
        "version": "2.1.0",
        "runs": [{"results": [
            {
                "ruleId": rule,
                "level": level,
                "message": {"text": msg},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": file}, "region": {"startLine": line}}}],
                "properties": {"actual": line},
            }
            for rule, level, file, line, msg in results
        ]}],
    }  # fmt: skip


# ── settings ──────────────────────────────────────────────────────────────────────────────────


def test_tool_versions_are_pinned() -> None:
    settings = load_settings()
    assert settings.qlty == "0.654.0"
    for spec in (settings.aislop, settings.pyscn, settings.fallow, settings.cspell):
        assert any(sep in spec for sep in ("@", "==")), spec


# ── qlty helpers, radarlint, smells ──────────────────────────────────────────────────────────


def test_sarif_results_split_plugin_and_rule() -> None:
    rows = qlty.results(
        sarif(("radarlint-python:python:S5713", "error", "apps/a/x.py", 4, "Remove this redundant Exception"))
    )
    assert rows == [{
        "plugin": "radarlint-python", "rule": "python:S5713", "severity": "warning", "file": "apps/a/x.py",
        "line": 4, "end_line": None, "msg": "Remove this redundant Exception", "properties": {"actual": 4},
    }]  # fmt: skip


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("apps/agents_server/tests/core/test_x.py", True),
        ("apps/hanbot_ui/src/lib/run-window.test.ts", True),
        ("apps/agents_server/app/core/x.py", False),
        ("apps/hanbot_ui/src/lib/run-window.ts", False),
    ],
)
def test_is_test(path: str, expected: bool) -> None:
    assert qlty.is_test(path) is expected


def test_wrong_qlty_version_is_a_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(qlty.shutil, "which", lambda _: "/usr/bin/qlty")
    monkeypatch.setattr(qlty, "run", lambda *a, **k: "qlty 0.600.0 macos-arm64\n")
    with pytest.raises(ToolError, match=r"0\.600\.0 installed"):
        qlty.check_version(ctx)


def test_radarlint_drops_overlapping_rules_and_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)
    rows = qlty.results(sarif(
        ("radarlint-python:python:S5713", "error", "apps/demo/pkg/a.py", 3, "redundant exception"),
        ("radarlint-python:python:S3776", "error", "apps/demo/pkg/a.py", 9, "cognitive complexity"),
        ("radarlint-python:python:S1244", "error", "apps/demo/tests/test_a.py", 5, "float equality"),
        ("radarlint-python:python:S125", "note", "apps/demo/pkg/b.py", 7, "commented-out code"),
    ))  # fmt: skip
    monkeypatch.setattr(qlty, "run_sarif", lambda *a, **k: rows)
    result = radarlint.collect(ctx)
    assert result["findings"] == 2
    assert result["by_rule"] == {"python:S5713": 1, "python:S125": 1}
    assert [i["severity"] for i in result["items"]] == ["warning", "info"]


def test_smells_split_structure_and_duplication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)
    rows = qlty.results(sarif(
        ("qlty:function-parameters", "warning", "apps/demo/pkg/a.py", 9, "Function with many parameters (count = 9)"),
        ("qlty:similar-code", "warning", "apps/demo/pkg/b.py", 20, "Found 26 lines of similar code"),
        ("qlty:return-statements", "warning", "apps/demo/tests/test_b.py", 6, "many returns"),
    ))  # fmt: skip
    monkeypatch.setattr(qlty, "run_sarif", lambda *a, **k: rows)
    result = smells.collect(ctx)
    assert (result["smells"], result["structure"], result["duplication"]) == (2, 1, 1)
    assert result["items"][0]["code"] == "similar-code"  # largest "actual" first


# ── aislop, pyscn, fallow, cspell ────────────────────────────────────────────────────────────


def test_aislop_drops_tuned_out_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)
    out = {
        "score": 86,
        "diagnostics": [
            {"filePath": "pkg/a.py", "line": 4, "rule": "ai-slop/silent-recovery", "engine": "ai-slop",
             "severity": "warning", "message": "except logs without the caught error then continues"},
            {"filePath": "pkg/a.py", "line": 9, "rule": "ai-slop/narrative-comment", "engine": "ai-slop",
             "severity": "info", "message": "decorative separator"},
            {"filePath": "pkg/b.py", "line": 2, "rule": "ai-slop/hardcoded-url", "engine": "ai-slop",
             "severity": "warning", "message": "Hardcoded environment URL"},
            {"filePath": "pkg/b.py", "line": 30, "rule": "complexity/too-many-params", "engine": "code-quality",
             "severity": "warning", "message": "Too many parameters"},
        ],
    }  # fmt: skip
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(aislop, "run", lambda *a, **k: json.dumps(out))
    result = aislop.collect(ctx)
    assert (result["score"], result["findings"], result["slop"]) == (86, 2, 1)
    assert result["items"][0]["file"] == "apps/demo/pkg/a.py"


def test_aislop_runs_without_cqs_environment_and_drops_its_python_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """aislop's pip-audit audits whatever environment it sits in; its npm audit audits the app."""
    own = tmp_path / "cq-env" / "bin"
    own.mkdir(parents=True)
    monkeypatch.setattr(aislop.sysconfig, "get_path", lambda name: str(own))
    monkeypatch.setenv("PATH", os.pathsep.join([str(own), "/usr/local/bin", "/usr/bin"]))
    out = {
        "score": 70,
        "diagnostics": [
            {"filePath": "requirements.txt", "line": 0, "rule": "security/vulnerable-dependency",
             "engine": "security", "severity": "error", "message": "Vulnerable Python dependency: certifi"},
            {"filePath": "package.json", "line": 0, "rule": "security/vulnerable-dependency",
             "engine": "security", "severity": "error", "message": "left-pad-ish (high) — upgrade to 1.2.0"},
        ],
    }  # fmt: skip
    paths: list[str] = []
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(aislop, "run", lambda *a, env, **k: paths.append(env["PATH"]) or json.dumps(out))
    result = aislop.collect(make_ctx(tmp_path))
    assert paths == [os.pathsep.join(["/usr/local/bin", "/usr/bin"])]
    assert [i["msg"] for i in result["items"]] == ["left-pad-ish (high) — upgrade to 1.2.0"]
    assert (result["score"], result["findings"]) == (70, 1)  # aislop's own score stays as reported


def test_a_system_pythons_shared_scripts_folder_stays_on_aislops_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside a virtual environment the scripts folder may be /usr/local/bin, where npx lives too."""
    monkeypatch.setattr(aislop.sys, "prefix", "/usr/local")
    monkeypatch.setattr(aislop.sys, "base_prefix", "/usr/local")
    monkeypatch.setattr(aislop.sysconfig, "get_path", lambda name: "/usr/local/bin")
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/local/bin", "/usr/bin"]))
    assert aislop.path_without_cq() == os.pathsep.join(["/usr/local/bin", "/usr/bin"])


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx not installed")
def test_aislop_never_runs_the_pip_audit_of_cqs_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real aislop scan, with a pip-audit in "cq's environment" that would report a vulnerable package."""
    own = tmp_path / "cq-env" / "bin"
    own.mkdir(parents=True)
    ran = tmp_path / "pip-audit-ran"
    report = {"dependencies": [{"name": "certifi", "version": "1.0", "vulns": [{"id": "PYSEC-0000-0"}]}]}
    fake = own / "pip-audit"
    fake.write_text(f"#!/bin/sh\ntouch {ran}\necho '{json.dumps(report)}'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(aislop.sysconfig, "get_path", lambda name: str(own))
    monkeypatch.setenv("PATH", f"{own}{os.pathsep}{os.environ['PATH']}")
    app = tmp_path / "apps" / "demo"
    (app / "pkg").mkdir(parents=True)
    (app / "pyproject.toml").write_text("[project]\nname = 'demo'\nversion = '0.1.0'\n")
    (app / "pkg" / "__init__.py").write_text('"""Demo."""\n')
    result = aislop.collect(make_ctx(tmp_path, aislop=PINS["aislop"]))
    assert not ran.exists()
    assert not [i for i in result["items"] if i["code"] == "security/vulnerable-dependency"]


def test_aislop_without_json_is_a_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(aislop, "run", lambda *a, **k: "npm ERR! something")
    with pytest.raises(ToolError, match="no JSON"):
        aislop.collect(make_ctx(tmp_path))


def test_pyscn_keeps_long_production_clones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)

    def pair(a: str, b: str, lines: int, sim: float) -> dict:
        return {
            "type": 2,
            "similarity": sim,
            "clone1": {"location": {"file_path": a, "start_line": 10}, "line_count": lines},
            "clone2": {"location": {"file_path": b, "start_line": 40}, "line_count": lines + 1},
        }

    out = {
        "summary": {"health_score": 83, "code_duplication_percentage": 9.123, "deps_modules_in_cycles": 0,
                    "high_coupling_classes": 0, "high_lcom_classes": 1},
        "clone": {"clone_pairs": [
            pair("apps/demo/pkg/triage_a.py", "apps/demo/pkg/triage_b.py", 30, 0.84),
            pair("apps/demo/pkg/x.py", "apps/demo/pkg/y.py", 3, 1.0),
            pair("apps/demo/tests/test_x.py", "apps/demo/tests/test_y.py", 20, 0.9),
        ]},
    }  # fmt: skip
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(pyscn, "run", lambda *a, **k: json.dumps(out))
    result = pyscn.collect(ctx)
    assert (result["clone_pairs"], result["duplication_percent"], result["low_cohesion_classes"]) == (1, 9.12, 1)
    assert result["items"][0]["a"] == "apps/demo/pkg/triage_a.py:10"
    assert (result["items"][0]["lines"], result["items"][0]["b_lines"]) == (30, 31)


def test_fallow_reads_dupes_and_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path, kind="typescript")
    (ctx.app_dir / "node_modules").mkdir()
    dupes = {
        "stats": {"duplication_percentage": 3.83, "clone_groups": 1},
        "clone_groups": [{"fingerprint": "dup:1", "line_count": 9, "instances": [
            {"file": "src/a.tsx", "start_line": 66}, {"file": "src/b.tsx", "start_line": 69}]}],
    }  # fmt: skip
    health = {
        "health_score": {"score": 86.1},
        "summary": {"functions_above_threshold": 2, "severity_critical_count": 1, "severity_high_count": 1},
        "findings": [{"path": "src/page.tsx", "name": "Page", "line": 42, "severity": "critical",
                      "cyclomatic": 79, "cognitive": 74, "crap": 6320.0}],
    }  # fmt: skip
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(fallow, "run", lambda cmd, *a, **k: json.dumps(dupes if "dupes" in cmd else health))
    result = fallow.collect(ctx)
    assert (result["duplication_percent"], result["critical"], result["health_score"]) == (3.83, 1, 86.1)
    assert result["clones"][0] == {
        "fingerprint": "dup:1",
        "lines": 9,
        "a": "apps/demo/src/a.tsx:66",
        "b": "apps/demo/src/b.tsx:69",
    }
    assert result["items"][0]["severity"] == "warning"


def test_fallow_needs_node_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    with pytest.raises(ToolError, match="node_modules"):
        fallow.collect(make_ctx(tmp_path, kind="typescript"))


def test_cspell_counts_unknown_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)
    out = (
        "apps/demo/pkg/a.py:19:22 - Unknown word (hyperglicemia)\n"
        "apps/demo/pkg/b.py:3:5 - Unknown word (Placeholdername)\n"
        "apps/demo/pkg/c.py:8:1 - Unknown word (Placeholdername)\n"
        "CSpell: Files checked: 3\n"
    )
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(cspell, "run", lambda *a, **k: out)
    result = cspell.collect(ctx)
    assert (result["unknown_words"], result["occurrences"]) == (2, 3)
    assert "Placeholdername" not in json.dumps(result)  # the words are never stored
    assert not list(ctx.raw_dir.iterdir())


def test_cspell_default_has_no_project_words() -> None:
    config = json.loads((DEFAULTS_DIR / "cspell.json").read_text())
    assert config["words"] == []  # a project's words belong in its own cspell.json


# ── vale (real binary) ───────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("vale") is None, reason="vale not installed")
def test_vale_flags_ai_prose_and_stubs(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text(
        "# Notes\n\nLet us delve into this seamless design.\n\nIn a real implementation this would retry.\n"
    )
    ctx = make_ctx(tmp_path, link_paths=("docs",))
    result = vale.collect(ctx)
    assert result["prose"] == 2
    assert result["stubs"] == 1
    assert {i["file"] for i in result["items"]} == {"docs/a.md"}


# ── secrets ──────────────────────────────────────────────────────────────────────────────────

TRUFFLEHOG_LINE = json.dumps({
    "DetectorName": "MongoDB",
    "Verified": True,
    "Raw": "SECRETVALUE",
    "Redacted": "SECRET*****",
    "SourceMetadata": {"Data": {"Git": {"file": "nightscout/env", "line": 12, "commit": "fdd3b6a0123",
                                        "timestamp": "2025-10-26 10:00:00 +0000"}}},
})  # fmt: skip


def test_trufflehog_rows_keep_no_secret() -> None:
    rows = secrets.parse_trufflehog(f"some log line\n{TRUFFLEHOG_LINE}\n")
    assert rows == [{"detector": "MongoDB", "identity": "7d39b682afae", "verified": True, "file": "nightscout/env", "line": 12,
                     "commit": "fdd3b6a", "date": "2025-10-26", "unverifiable": False}]  # fmt: skip
    assert "SECRETVALUE" not in json.dumps(rows)


def test_secret_output_never_reaches_the_log(tmp_path: Path) -> None:
    log_file = tmp_path / "tool.log"
    with log_to(log_file):
        # The value comes in through the environment, as a scanner prints what it found on stdout.
        out = run(["sh", "-c", 'echo "$V"'], tmp_path, env={"V": "SECRETVALUE"}, secret_output=True)
    assert out.strip() == "SECRETVALUE"
    assert "SECRETVALUE" not in log_file.read_text()
    assert "withheld" in log_file.read_text()


def test_secret_output_stays_out_of_errors(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as excinfo:
        run(["sh", "-c", 'echo "$V"; echo "$V" >&2; exit 3'], tmp_path, env={"V": "SECRETVALUE"}, secret_output=True)
    assert "SECRETVALUE" not in str(excinfo.value)
    assert (excinfo.value.returncode, excinfo.value.stdout, excinfo.value.stderr) == (3, "", "")


def test_a_failed_command_s_error_carries_its_exit_code_and_output(tmp_path: Path) -> None:
    with pytest.raises(ToolError) as excinfo:
        run(["sh", "-c", "echo out; echo err >&2; exit 2"], tmp_path)
    assert (excinfo.value.returncode, excinfo.value.stdout, excinfo.value.stderr) == (2, "out\n", "err\n")


@pytest.mark.skipif(shutil.which("betterleaks") is None, reason="betterleaks not installed")
def test_betterleaks_findings_are_slim_and_nothing_is_kept(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text('GITHUB_TOKEN = "ghp_' + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5" + '"\n')
    found = secrets.betterleaks(["dir", str(tmp_path)], tmp_path)
    assert len(found) == 1
    assert set(found[0]) == {"rule", "file", "line", "commit", "date", "fingerprint"}
    assert "ghp_" not in json.dumps(found)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.py"]  # no report left behind


def test_secrets_requires_both_scanners(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proc.shutil, "which", lambda tool: None if tool == "trufflehog" else "/x")
    with pytest.raises(ToolError, match="trufflehog"):
        proc.require("betterleaks", "trufflehog")


# ── findings ─────────────────────────────────────────────────────────────────────────────────


def test_new_collectors_become_findings() -> None:
    snap = {
        "targets": {
            "demo": {
                "radarlint": {"items": [{"file": "a.py", "line": 3, "code": "python:S5713", "severity": "warning", "msg": "redundant"}]},
                "smells": {"items": [{"file": "a.py", "line": 9, "code": "function-parameters", "msg": "count = 9"}]},
                "aislop": {"items": [{"file": "a.py", "line": 4, "code": "ai-slop/silent-recovery", "severity": "warning", "msg": "lost cause"}]},
                "pyscn": {"items": [{"a": "a.py:10", "b": "b.py:40", "lines": 30, "similarity": 0.84, "type": 2}]},
                "vale": {"items": [{"file": "doc.md", "line": 1, "code": "Stubs", "severity": "error", "match": "In a real implementation", "msg": "stub"}]},
                "cspell": {"items": [{"file": "a.py", "line": 19}]},
            },
            "repo": {
                "secrets": {"live_items": [{"file": "nightscout/env", "line": 12, "code": "MongoDB", "commit": "fdd3b6a", "date": "2025-10-26"}]},
                "scripts": {"workflow_security": {"items": [{"file": ".github/workflows/check.yml", "line": 28, "code": "zizmor/unpinned-uses", "msg": "unpinned"}]}},
            },
        },
        "errors": {},
    }  # fmt: skip
    found = {(f["tool"], f["rule"], f["severity"]) for f in findings.from_snapshot(snap)}
    assert found == {
        ("radarlint", "python:S5713", "warning"),
        ("smells", "function-parameters", "warning"),
        ("aislop", "ai-slop/silent-recovery", "warning"),
        ("pyscn", "clone-type-2", "info"),
        ("vale", "Stubs", "error"),
        ("cspell", "unknown-word", "info"),
        ("secrets", "live-MongoDB", "error"),
        ("scripts", "zizmor-zizmor/unpinned-uses", "warning"),
    }


def trufflehog_line(detector: str, file: str, commit: str, verified: bool, error: str = "") -> str:
    """One trufflehog JSON line, with a fake secret that must never come out."""
    return json.dumps({
        "DetectorName": detector, "Verified": verified, "VerificationError": error, "Raw": "SECRETVALUE",
        "SourceMetadata": {"Data": {"Git": {"file": file, "line": 1, "commit": commit, "timestamp": "2025-10-26"}}},
    })  # fmt: skip


def test_scan_verified_dedups_and_puts_live_keys_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = "\n".join([
        trufflehog_line("Postgres", "a.env", "aaaaaaa", False),
        trufflehog_line("MongoDB", "b.env", "bbbbbbb", True),
        trufflehog_line("MongoDB", "b.env", "bbbbbbb", True),
    ])  # fmt: skip
    monkeypatch.setattr(secrets, "run", lambda *a, **k: out)
    rows = secrets.scan_verified(make_ctx(tmp_path))
    assert [(r["detector"], r["verified"]) for r in rows] == [("MongoDB", True), ("Postgres", False)]


def test_scan_verified_fails_when_nothing_could_be_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = trufflehog_line("MongoDB", "b.env", "bbbbbbb", False, error="dial tcp: no route to host")
    monkeypatch.setattr(secrets, "run", lambda *a, **k: out)
    with pytest.raises(ToolError, match="verify none"):
        secrets.scan_verified(make_ctx(tmp_path))


def test_collect_counts_distinct_live_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = {
        "detector": "MongoDB",
        "verified": True,
        "file": "b.env",
        "line": 1,
        "date": "2025-10-25",
        "unverifiable": False,
    }
    rows = [live | {"commit": "c1", "identity": "k1"}, live | {"commit": "c2", "identity": "k1"},  # one key, 2 commits
            live | {"commit": "c2", "line": 2, "identity": "k2"},  # a second key in the same file
            live | {"detector": "OpenAI", "commit": "c3", "identity": "k3"},
            live | {"detector": "Postgres", "verified": False, "unverifiable": True, "commit": "c4", "identity": "k4"}]  # fmt: skip
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/x")
    monkeypatch.setattr(secrets, "scan_tree", lambda ctx: [])
    monkeypatch.setattr(secrets, "scan_history", lambda ctx: [])
    monkeypatch.setattr(secrets, "scan_verified", lambda ctx: rows)
    monkeypatch.setattr(secrets, "head_paths", lambda checkout, places: {p: p[0] for p in places})
    result = secrets.collect(make_ctx(tmp_path))
    assert (result["verified_live"], result["unverifiable"], len(result["live_items"])) == (3, 1, 3)
    assert next(i for i in result["live_items"] if i["code"] == "MongoDB" and i["line"] == 1)["commit"] == "c2"


def test_history_cache_is_betterleaks_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
                   cwd=tmp_path, check=True)  # fmt: skip
    ctx = make_ctx(tmp_path, data_dir=tmp_path / ".cq", repo_root=tmp_path)
    cache = ctx.settings.runs_dir / "_cache"
    cache.mkdir(parents=True)
    (cache / "gitleaks-history-x-y.json").write_text("{}")  # the old scanner's cache: ignored
    monkeypatch.setattr(secrets, "betterleaks", lambda args, cwd, config: [])
    monkeypatch.setattr(secrets, "config_hash", lambda config: "h")
    secrets.scan_history(ctx)
    names = sorted(p.name.split("-")[0] for p in cache.iterdir())
    assert names == ["betterleaks", "gitleaks"]


def test_stderr_is_withheld_too(tmp_path: Path) -> None:
    log_file = tmp_path / "tool.log"
    with log_to(log_file), pytest.raises(ToolError) as excinfo:
        run(["sh", "-c", 'echo "$V" >&2; exit 2'], tmp_path, env={"V": "SECRETVALUE"}, secret_output=True)
    assert "SECRETVALUE" not in log_file.read_text()
    assert "SECRETVALUE" not in str(excinfo.value)


def test_old_checkout_gets_the_current_qlty_config(tmp_path: Path) -> None:
    source, old = tmp_path / "repo", tmp_path / "old"
    (source / ".qlty" / "configs").mkdir(parents=True)
    (source / ".qlty" / "qlty.toml").write_text("current")
    (source / ".qlty" / "configs" / ".shellcheckrc").write_text("rc")
    (old / ".qlty" / "configs").mkdir(parents=True)
    (old / ".qlty" / "qlty.toml").write_text("old")
    (old / ".qlty" / "configs" / ".obsolete").write_text("dropped since")
    qlty.use_current_config(old, source)
    assert not (old / ".qlty" / "configs" / ".obsolete").exists()
    assert (old / ".qlty" / "qlty.toml").read_text() == "current"
    assert (old / ".qlty" / "configs" / ".shellcheckrc").read_text() == "rc"


def test_current_checkout_config_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / ".qlty").mkdir()
    (tmp_path / ".qlty" / "qlty.toml").write_text("current")
    qlty.use_current_config(tmp_path, tmp_path)
    assert (tmp_path / ".qlty" / "qlty.toml").read_text() == "current"


def test_trufflehog_is_scoped_to_the_revision_and_fails_on_scan_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(secrets, "run", lambda cmd, *a, **k: calls.append(cmd) or "")
    secrets.scan_verified(make_ctx(tmp_path))
    assert {"--branch", "HEAD", "--skip-additional-refs", "--fail-on-scan-errors"} <= set(calls[0])


def test_live_items_put_the_newest_keys_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = [{"detector": "MongoDB", "verified": True, "file": f"f{n}", "line": 1, "commit": f"o{n}",
            "date": "2024-01-01", "unverifiable": False, "identity": f"k{n}"} for n in range(45)]  # fmt: skip
    new = old[0] | {"file": "new.env", "commit": "n1", "date": "2026-10-01"}
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/x")
    monkeypatch.setattr(secrets, "scan_tree", lambda ctx: [])
    monkeypatch.setattr(secrets, "scan_history", lambda ctx: [])
    monkeypatch.setattr(secrets, "scan_verified", lambda ctx: [*old, new])
    monkeypatch.setattr(secrets, "head_paths", lambda checkout, places: {p: p[0] for p in places})
    items = secrets.collect(make_ctx(tmp_path))["live_items"]
    assert items[0]["file"] == "new.env"


def test_a_rotated_live_key_is_a_new_finding() -> None:
    item = {"file": "b.env", "line": 3, "code": "MongoDB", "commit": "aaaaaaa", "date": "2025-01-01"}
    rotated = item | {"commit": "bbbbbbb", "date": "2026-10-01"}
    keys = [
        f["key"]
        for d in ({"live_items": [item]}, {"live_items": [rotated]})
        for f in findings.extract("repo", "secrets", d)
    ]
    assert keys[0] != keys[1]


def test_two_live_keys_in_one_commit_and_file_are_both_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = "\n".join([trufflehog_line("MongoDB", "b.env", "bbbbbbb", True),
                      trufflehog_line("MongoDB", "b.env", "bbbbbbb", True).replace('"line": 1', '"line": 2')])  # fmt: skip
    monkeypatch.setattr(secrets, "run", lambda *a, **k: out)
    assert len(secrets.scan_verified(make_ctx(tmp_path))) == 2


def test_two_zizmor_findings_of_one_rule_get_distinct_keys() -> None:
    items = [{"file": "w.yml", "line": n, "code": "unpinned-uses", "msg": "m", "at": f"h{n}"} for n in (5, 9)]
    keys = {f["key"] for f in findings.extract("repo", "scripts", {"workflow_security": {"items": items}})}
    assert len(keys) == 2


@pytest.mark.parametrize("tool", ["radarlint", "smells", "aislop", "vale"])
def test_repeated_hits_in_one_file_get_distinct_stable_keys(tool: str) -> None:
    hit = {"file": "a.py", "code": "S1", "severity": "warning", "msg": "same", "match": "delve"}
    before = {"items": [hit | {"line": 10}]}
    after = {"items": [hit | {"line": 14}, hit | {"line": 40}]}  # lines shifted, and a second hit added
    keys = [[f["key"] for f in findings.extract("repo", tool, d)] for d in (before, after)]
    assert keys[0][0] == keys[1][0]
    assert len(set(keys[1])) == 2


def test_cspell_output_is_kept_out_of_the_tool_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_ctx(tmp_path)
    calls: list[dict] = []
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(cspell, "run", lambda *a, **k: calls.append(k) or "")
    cspell.collect(ctx)
    assert calls[0]["secret_output"] is True


def test_a_hit_prepended_above_an_old_one_is_the_new_finding() -> None:
    hit = {"file": "a.py", "code": "S1", "severity": "warning", "msg": "same", "match": "delve"}
    before = {"items": [hit | {"line": 10, "at": "old"}]}
    after = {"items": [hit | {"line": 4, "at": "new"}, hit | {"line": 12, "at": "old"}]}
    old_keys = {f["key"] for f in findings.extract("repo", "radarlint", before)}
    new = [f for f in findings.extract("repo", "radarlint", after) if f["key"] not in old_keys]
    assert [f["line"] for f in new] == [4]


def test_fingerprint_follows_the_line_text_and_hides_it(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n\nfamily_name = 'Placeholdername'\n")
    (tmp_path / "b.py").write_text("\n\n\n\nfamily_name = 'Placeholdername'\n")
    assert ctx.fingerprint("a.py", 3) == ctx.fingerprint("b.py", 5) != ""
    assert "Placeholdername" not in ctx.fingerprint("a.py", 3)
    assert ctx.fingerprint("a.py", 2) == ctx.fingerprint("a.py", 99) == ctx.fingerprint("missing.py", 1) == ""


def test_two_clone_pairs_between_the_same_files_get_distinct_keys() -> None:
    pair = {"b": "y.py:30", "lines": 8, "similarity": 0.9, "type": 2}
    items = [pair | {"a": "x.py:5", "at": "1~2"}, pair | {"a": "x.py:50", "at": "3~4"}]
    assert len({f["key"] for f in findings.extract("repo", "pyscn", {"items": items})}) == 2


def test_a_clone_finding_carries_both_copies_lengths_when_known() -> None:
    pair = {"a": "x.py:5", "b": "y.py:30", "at": "1~2", "lines": 8, "similarity": 0.9, "type": 2}
    (old,) = findings.extract("repo", "pyscn", {"items": [pair]})
    (new,) = findings.extract("repo", "pyscn", {"items": [pair | {"b_lines": 9}]})
    assert "copy_lines" not in old
    assert new["copy_lines"] == [8, 9]
    assert new["key"] == old["key"]  # the key doesn't move


def test_trufflehog_identity_is_a_hash_not_the_secret() -> None:
    rows = secrets.parse_trufflehog(trufflehog_line("MongoDB", "b.env", "bbbbbbb", True))
    assert rows[0]["identity"] and "SECRETVALUE" not in json.dumps(rows)


def test_cspell_keys_survive_lines_shifting(tmp_path: Path) -> None:
    item = {"file": "a.py", "at": "abc"}
    keys = [findings.extract("repo", "cspell", {"items": [item | {"line": n}]})[0]["key"] for n in (3, 9)]
    assert keys[0] == keys[1]


def test_two_credentials_on_one_line_are_both_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = "\n".join([trufflehog_line("MongoDB", "b.env", "bbbbbbb", True),
                      trufflehog_line("MongoDB", "b.env", "bbbbbbb", True).replace("SECRETVALUE", "OTHERVALUE")])  # fmt: skip
    monkeypatch.setattr(secrets, "run", lambda *a, **k: out)
    assert len(secrets.scan_verified(make_ctx(tmp_path))) == 2


def test_one_credential_in_many_places_takes_one_live_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"detector": "MongoDB", "verified": True, "line": 1, "unverifiable": False}
    rows = [
        row | {"identity": "k1", "file": f"f{n}", "commit": f"c{n}", "date": f"2024-01-{n + 1:02d}"} for n in range(9)
    ]
    rows.append(row | {"identity": "k2", "file": "other.env", "commit": "c99", "date": "2023-01-01"})
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/x")
    monkeypatch.setattr(secrets, "scan_tree", lambda ctx: [])
    monkeypatch.setattr(secrets, "scan_history", lambda ctx: [])
    monkeypatch.setattr(secrets, "scan_verified", lambda ctx: rows)
    monkeypatch.setattr(secrets, "head_paths", lambda checkout, places: {p: p[0] for p in places})
    items = secrets.collect(make_ctx(tmp_path))["live_items"]
    assert [(i["file"], i["commit"]) for i in items] == [("f8", "c8"), ("other.env", "c99")]


def test_aislop_scans_without_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(proc.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(aislop, "run", lambda cmd, *a, **k: calls.append(cmd) or '{"diagnostics": []}')
    aislop.collect(make_ctx(tmp_path))
    excluded = calls[0][calls[0].index("--exclude") + 1].split(",")
    assert {"**/tests/**", "**/test_*.py", "**/*.test.tsx"} <= set(excluded)


def test_fallow_functions_of_one_name_get_distinct_keys() -> None:
    fn = {"file": "a.ts", "function": "handle", "severity": "warning", "cyclomatic": 20, "cognitive": 30, "crap": 50}
    items = [fn | {"line": 5, "at": "classA"}, fn | {"line": 80, "at": "classB"}]
    assert len({f["key"] for f in findings.extract("repo", "fallow", {"items": items})}) == 2


def test_history_cache_is_keyed_by_the_betterleaks_version(monkeypatch: pytest.MonkeyPatch) -> None:
    hashes = []
    for version in ("1.9.0", "1.10.0"):
        monkeypatch.setattr(secrets, "run", lambda *a, v=version, **k: v)
        hashes.append(secrets.config_hash(secrets.DEFAULT_CONFIG))
    assert hashes[0] != hashes[1]


def test_vale_never_reads_excluded_folders(tmp_path: Path) -> None:
    for rel in ("docs/a.md", "docs/runs/r1/analysis.md", "docs/sub/node_modules/x.md", "docs/sub/b.py", "docs/c.txt"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    ctx = make_ctx(tmp_path, link_paths=("docs",), link_exclude=("docs/runs", "node_modules"))
    assert vale.paths(ctx) == ["docs/a.md", "docs/sub/b.py"]


def test_zizmor_keys_survive_lines_shifting() -> None:
    item = {"file": "w.yml", "code": "unpinned-uses", "msg": "m", "at": "h"}
    keys = [
        findings.extract("repo", "scripts", {"workflow_security": {"items": [item | {"line": n}]}})[0]["key"]
        for n in (5, 30)
    ]
    assert keys[0] == keys[1]


def git(cwd: Path, *args: str) -> None:
    """A git command in a test repo."""
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)  # fmt: skip


def test_head_paths_follow_renames_and_deletions(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    for name in ("moved.env", "gone.env", "stays.env", "café®.env", "tab\tname.env"):
        (tmp_path / name).write_text(f"{name}\n" * 5)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "add")
    (tmp_path / "research").mkdir()
    git(tmp_path, "mv", "moved.env", "research/moved.env")
    git(tmp_path, "mv", "tab\tname.env", "research/tab\tname.env")
    git(tmp_path, "rm", "-q", "gone.env")
    git(tmp_path, "commit", "-qm", "reorganise")
    first = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD~1"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    # gone.env's path is reused by an unrelated file: the secret's file is still the deleted one
    (tmp_path / "gone.env").write_text("another file\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "reuse the name")
    places = {("moved.env", first), ("gone.env", first), ("stays.env", first), ("café®.env", first),
              ("tab\tname.env", first)}  # fmt: skip
    found = secrets.head_paths(tmp_path, places)
    assert found == {
        ("café®.env", first): "café®.env",  # a non-ASCII name is matched unquoted
        ("tab\tname.env", first): "research/tab\tname.env",  # and a tab survives the rename log
        ("moved.env", first): "research/moved.env",
        ("gone.env", first): "deleted",
        ("stays.env", first): "stays.env",
    }


def test_history_findings_say_where_the_file_is_now() -> None:
    item = {"file": "nightscout/env", "head_file": "research/nightscout/env", "line": 5, "code": "MongoDB",
            "commit": "fdd3b6a", "date": "2025-10-25"}  # fmt: skip
    gone = item | {"head_file": "deleted"}
    found = list(findings.extract("repo", "secrets", {"live_items": [item], "items": [gone]}))
    by_rule = {f["rule"]: f for f in found}
    assert by_rule["live-MongoDB"]["head_file"] == "research/nightscout/env"
    assert "now research/nightscout/env" in by_rule["live-MongoDB"]["message"]
    assert "since been deleted" in by_rule["in-history-MongoDB"]["message"]
    assert by_rule["live-MongoDB"]["file"] == "nightscout/env"  # the key keeps the historical path


def test_head_paths_ignore_a_side_branch_rename_the_merge_dropped(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "old.env").write_text("a\nb\nc\nd\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "add")
    first = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    git(tmp_path, "checkout", "-qb", "side")
    git(tmp_path, "mv", "old.env", "new.env")
    git(tmp_path, "commit", "-qm", "rename on a branch")
    git(tmp_path, "checkout", "-q", "main")
    git(tmp_path, "merge", "-q", "-s", "ours", "--no-edit", "side")  # the merge keeps old.env
    assert secrets.head_paths(tmp_path, {("old.env", first)}) == {("old.env", first): "old.env"}


def test_head_paths_follow_a_rename_made_on_the_branch_before_it_joined(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "base.txt").write_text("base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "side")
    (tmp_path / "old.env").write_text("a\nb\nc\nd\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "add the secret on the branch")
    found = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    git(tmp_path, "mv", "old.env", "new.env")
    git(tmp_path, "commit", "-qm", "rename on the branch")
    git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "main.txt").write_text("main\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "work on main")
    git(tmp_path, "merge", "-q", "--no-edit", "side")
    assert secrets.head_paths(tmp_path, {("old.env", found)}) == {("old.env", found): "new.env"}


def test_a_qlty_setup_without_configs_folder_mirrors_its_absence(tmp_path: Path) -> None:
    source, old = tmp_path / "repo", tmp_path / "old"
    (source / ".qlty").mkdir(parents=True)
    (source / ".qlty" / "qlty.toml").write_text("current")  # what cq init writes: no configs/
    (old / ".qlty" / "configs").mkdir(parents=True)
    (old / ".qlty" / "configs" / ".stale").write_text("from an old commit")
    qlty.use_current_config(old, source)
    assert (old / ".qlty" / "qlty.toml").read_text() == "current"
    assert not (old / ".qlty" / "configs").exists()


def test_a_trufflehog_finding_without_its_git_source_is_an_error() -> None:
    line = json.dumps({"DetectorName": "AWS", "Verified": False, "SourceMetadata": {"Data": {}}})
    with pytest.raises(KeyError, match="Git"):
        secrets.parse_trufflehog(line)


def test_sarif_without_a_region_is_a_whole_file_finding() -> None:
    result = {
        "ruleId": "smells:file-complexity",
        "level": "warning",
        "message": {"text": "complex file"},
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "cq/findings.py"}}}],
    }
    [row] = qlty.results({"runs": [{"results": [result]}]})
    assert (row["file"], row["line"], row["end_line"], row["msg"]) == ("cq/findings.py", None, None, "complex file")


def test_sarif_without_a_location_or_message_is_an_error_not_a_blank_finding() -> None:
    located = {"locations": [{"physicalLocation": {"artifactLocation": {"uri": "a.py"}}}]}
    with pytest.raises(KeyError, match="locations"):
        qlty.results({"runs": [{"results": [{"ruleId": "x:y", "message": {"text": "m"}}]}]})
    with pytest.raises(KeyError, match="message"):
        qlty.results({"runs": [{"results": [{"ruleId": "x:y", **located}]}]})


def test_qlty_sarif_without_a_required_field_is_a_tool_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qlty, "check_version", lambda ctx: None)
    monkeypatch.setattr(qlty, "use_current_config", lambda checkout, repo_root: None)
    no_location = {"runs": [{"results": [{"ruleId": "smells:x", "message": {"text": "m"}}]}]}
    monkeypatch.setattr(qlty, "run", lambda cmd, cwd, ok_codes: json.dumps(no_location))
    with pytest.raises(ToolError, match="without 'locations'"):  # the gate's attempt() lists a ToolError
        qlty.run_sarif(make_ctx(tmp_path), ["smells", "--all"], "smells")
