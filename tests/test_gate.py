"""The PR gate (cq/gate.py, #284): what counts as new, what blocks, the ratchets, and the exit code.

git, qlty, betterleaks and the snapshots are replaced with recorded shapes; the end-to-end run on a
planted-slop branch is in the PR's Tests section.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from cq import gate, qlty
from cq.collectors import docstrings, secrets
from cq.config import GateSettings, Settings, Target, load_gate, load_settings
from cq.context import Context
from cq.proc import ToolError

APP = Target(name="app", kind="python", root="apps/app", source=("app",), tests=False)
UI = Target(name="ui", kind="typescript", root="apps/ui", source=("src",), tests=False)
REPO = Target(name="repo", kind="repo", root=".", source=(".",), tests=False)
TARGETS = (APP, UI, REPO)

DIFF = """diff --git a/apps/app/app/a.py b/apps/app/app/a.py
--- a/apps/app/app/a.py
+++ b/apps/app/app/a.py
@@ -3,0 +4,2 @@ def f():
+    x = 1
+    y = 2
@@ -10 +12 @@ def g():
-    old
+    new
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""


def snap(targets: dict, errors: dict | None = None) -> dict:
    """A snapshot shaped like runner.snapshot's."""
    return {"targets": targets, "errors": errors or {}}


def aislop_items(*items: dict) -> dict:
    """An app's tools with aislop's items."""
    return {"aislop": {"items": list(items)}}


def item(file: str, line: int, at: str, code: str = "ai-slop/todo-stub") -> dict:
    """One aislop item; ``at`` stands for its source line's fingerprint."""
    return {"file": file, "line": line, "at": at, "code": code, "severity": "warning", "msg": "m"}


# ── which lines, files and targets ────────────────────────────────────────────────────────────


def test_changed_lines_reads_hunks_and_ignores_deletions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "git", lambda *a: DIFF)
    assert gate.changed_lines("base") == {"apps/app/app/a.py": {4, 5, 12}}


def test_a_smell_counts_only_when_its_span_covers_a_changed_line() -> None:
    lines = {"a.py": {12}}
    assert gate.overlaps({"file": "a.py", "line": 10, "end_line": 14}, lines)
    assert not gate.overlaps({"file": "a.py", "line": 20, "end_line": 22}, lines)
    assert not gate.overlaps({"file": "b.py", "line": 12, "end_line": None}, lines)


def test_affected_targets_and_lockfiles() -> None:
    changed = ["apps/app/app/a.py", "apps/ui/package-lock.json", "wiki/x.md"]
    assert gate.affected(TARGETS, changed) == ["app", "ui", "repo"]
    assert gate.affected(TARGETS, ["scripts/check.sh"]) == []
    assert gate.lock_changed(TARGETS, changed) == {"ui"}


# ── line level and secrets ────────────────────────────────────────────────────────────────────


def test_line_level_keeps_checks_and_the_smells_on_changed_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"plugin": "qlty", "severity": "warning", "msg": "m", "properties": {}}
    sarif = {
        "gate-check": [row | {"plugin": "zizmor", "rule": "zizmor/unpinned-uses", "file": "w.yml", "line": 3}],
        "gate-smells": [
            row | {"rule": "function-parameters", "file": "apps/app/app/a.py", "line": 11, "end_line": 12},
            row | {"rule": "function-parameters", "file": "apps/app/app/a.py", "line": 30, "end_line": 31},
            row | {"rule": "file-complexity", "file": "apps/app/app/a.py", "line": 1, "end_line": 99},
            row | {"rule": "function-parameters", "file": "apps/app/tests/test_a.py", "line": 4, "end_line": 4},
        ],
    }
    monkeypatch.setattr(gate, "git", lambda *a: DIFF)
    monkeypatch.setattr(qlty, "run_sarif", lambda ctx, args, name: sarif[name])
    found = gate.line_level(None, "base")  # type: ignore[arg-type]
    assert [(f["tool"], f["rule"], f["line"]) for f in found] == [
        ("qlty", "zizmor:zizmor/unpinned-uses", 3),
        ("smells", "function-parameters", 11),
    ]


def test_branch_secrets_are_errors_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"rule": "generic-api-key", "file": "a.py", "line": 7, "commit": "abc1234", "date": "2026-10-14"}]
    calls: list[list[str]] = []
    monkeypatch.setattr(secrets, "betterleaks", lambda args, cwd, config: calls.append(args) or rows)
    found = gate.branch_secrets(SETTINGS, "base")
    assert calls[0][-1] == "--log-opts=base..HEAD"
    assert (found[0]["rule"], found[0]["severity"], found[0]["line"]) == ("in-branch-generic-api-key", "error", 7)


# ── compared tools ────────────────────────────────────────────────────────────────────────────


def test_compared_findings_are_new_by_key_and_in_changed_files() -> None:
    base = snap({"app": aislop_items(item("apps/app/app/a.py", 5, "old"))})
    head = snap(
        {
            "app": aislop_items(
                item("apps/app/app/a.py", 9, "old"),  # the base's finding, moved down: not new
                item("apps/app/app/a.py", 4, "new"),  # new, in a changed file
                item("apps/app/app/untouched.py", 2, "new"),  # new key, but a file the branch didn't change
            )
        }
    )
    new = gate.new_compared(head, base, {"apps/app/app/a.py"})
    assert [(f["file"], f["line"]) for f in new] == [("apps/app/app/a.py", 4)]


def test_an_app_the_base_lacks_has_all_its_findings_new() -> None:
    head = snap({"app": aislop_items(item("apps/app/app/a.py", 4, "x"))})
    base = snap({}, errors={"app": "not present at this commit"})
    assert len(gate.new_compared(head, base, {"apps/app/app/a.py"})) == 1
    assert gate.base_errors(base) == {}


def test_a_tool_that_failed_on_the_base_is_reported_not_compared() -> None:
    head = snap({"app": aislop_items(item("apps/app/app/a.py", 4, "x"))})
    base = snap({"app": {}}, errors={"app.aislop": "npx failed"})
    assert gate.new_compared(head, base, {"apps/app/app/a.py"}) == []
    assert gate.base_errors(base) == {"base: app.aislop": "npx failed"}


def test_a_clone_partner_is_matched_by_path_not_substring() -> None:
    clone = {"tool": "pyscn", "file": "x/old.py", "message": "8 lines 90% similar: x/old.py:3 and x/data.py:40"}
    assert gate.touches(clone, {"x/data.py"})
    assert not gate.touches(clone, {"a.py"})  # "a.py" is inside "data.py"


def test_a_clone_of_changed_code_counts_from_its_unchanged_side() -> None:
    clone = {"a": "apps/app/app/old.py:3", "b": "apps/app/app/a.py:40", "at": "1~2", "lines": 8,
             "similarity": 0.9, "type": 2}  # fmt: skip
    head = snap({"app": {"pyscn": {"items": [clone]}}})
    base = snap({"app": {"pyscn": {"items": []}}})
    assert len(gate.new_compared(head, base, {"apps/app/app/a.py"})) == 1


# ── ratchets ──────────────────────────────────────────────────────────────────────────────────


def counts(suppressions: int, critical: int = 0, over: dict | None = None) -> dict:
    """A snapshot with the ratchets' measures for one app."""
    return snap(
        {
            "app": {
                "suppressions": {"total": suppressions},
                "imports": {"violations": 0},
                "dependencies": {"issues": 1},
                "vulnerabilities": {"critical": critical, "high": 0},
                "cognitive": {"over_limit_items": over or {}},
            }
        }
    )


def test_ratchets_flag_what_got_worse() -> None:
    base = counts(3, over={"a.py::f": 20, "a.py::g": 18})
    head = counts(4, critical=1, over={"a.py::f": 20, "a.py::g": 19, "a.py::h": 16})
    found = gate.ratchets(head, base, locks=set())
    assert sorted(f["rule"] for f in found) == ["cognitive", "cognitive", "suppressions.total"]
    assert {f["symbol"] for f in found if f["rule"] == "cognitive"} == {"a.py::g", "a.py::h"}
    # the vulnerability ratchet runs only when the app's lockfile changed
    assert "vulnerabilities.critical" in {f["rule"] for f in gate.ratchets(head, base, locks={"app"})}


def test_ratchets_skip_tools_that_failed_on_the_base() -> None:
    base = snap({"app": {}}, errors={"app.suppressions": "boom", "app.cognitive": "boom", "app.dependencies": "boom"})
    assert gate.ratchets(counts(9, over={"a.py::f": 20}), base, locks=set()) == []


# ── blocking, summary and exit code ───────────────────────────────────────────────────────────


def test_blocking_list() -> None:
    patterns = ("qlty:zizmor:*", "ratchet:*")
    assert gate.blocks({"tool": "qlty", "rule": "ruff:F401", "severity": "error"}, patterns)
    assert gate.blocks({"tool": "qlty", "rule": "zizmor:zizmor/unpinned-uses", "severity": "warning"}, patterns)
    assert gate.blocks({"tool": "ratchet", "rule": "cognitive", "severity": "warning"}, patterns)
    assert not gate.blocks({"tool": "qlty", "rule": "ripgrep:TODO", "severity": "info"}, patterns)


def test_load_gate_refuses_an_unknown_mode(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.cq.gate]\nmode = "strict"\n')
    with pytest.raises(ValueError, match="warn or block"):
        load_gate(load_settings(tmp_path))
    pyproject.write_text('[tool.cq.gate]\nblock = ["smells:*"]\n')
    assert load_gate(load_settings(tmp_path)) == GateSettings(mode="warn", block=("smells:*",))


def test_load_gate_reads_cq_toml_at_its_top_level(tmp_path: Path) -> None:
    (tmp_path / "cq.toml").write_text('[gate]\nmode = "block"\n')
    assert load_gate(load_settings(tmp_path)).mode == "block"


def test_a_tool_error_is_recorded_not_raised() -> None:
    errors: dict[str, str] = {}

    def broken() -> list:
        raise ToolError("qlty 0.600.0 installed")

    assert gate.attempt("qlty", errors, broken) == []
    assert errors == {"qlty": "qlty 0.600.0 installed"}


def fake_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, found: list[dict]) -> Settings:
    """run_gate over stubbed git, line level, secrets and snapshots; the settings write under tmp_path."""
    answers = {("merge-base",): "basesha1234", ("rev-parse",): "head123"}
    monkeypatch.setattr(gate, "git", lambda *a: answers.get(a[:1], ""))
    monkeypatch.setattr(gate, "line_level", lambda ctx, base: found)
    monkeypatch.setattr(gate, "branch_secrets", lambda settings, base: [])
    return replace(SETTINGS, repo_root=tmp_path, data_dir=tmp_path / ".cq")


SETTINGS = Settings(targets=TARGETS, jscpd="j", knip="k", complexity_limit=15, cognitive_limit=15)
FINDING = {"target": "repo", "tool": "smells", "rule": "function-parameters", "severity": "warning",
           "file": "a.py", "line": 3, "symbol": "", "message": "9 parameters", "key": "k"}  # fmt: skip


@pytest.mark.parametrize(("mode", "report_only", "code"), [("warn", False, 0), ("block", False, 1), ("block", True, 0)])
def test_exit_code_by_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: Literal["warn", "block"], report_only: bool, code: int
) -> None:
    settings = fake_run(monkeypatch, tmp_path, [FINDING])
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    gate_settings = GateSettings(mode=mode, block=("smells:*",))
    assert gate.run_gate(settings, gate_settings, "origin/main", report_only=report_only).exit_code == code
    assert "**1 blocking**" in summary_file.read_text()
    out_dir = next(settings.runs_dir.glob("*_gate_head123"))
    row = json.loads((out_dir / "gate-findings.jsonl").read_text())
    assert (row["new"], row["blocking"]) == (True, True)


def test_a_clean_branch_passes_in_block_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = fake_run(monkeypatch, tmp_path, [])
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gate.run_gate(settings, GateSettings(mode="block"), "origin/main").exit_code == 0
    assert "Nothing new" in next(settings.runs_dir.glob("*_gate_*/gate.md")).read_text()
    assert (settings.data_dir / ".gitignore").read_text().endswith("*\n")  # a new data folder ignores itself


def test_a_tool_that_did_not_run_fails_block_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = fake_run(monkeypatch, tmp_path, [])
    answers = {("merge-base",): "basesha1234", ("rev-parse",): "head123", ("diff",): "M\tapps/app/app/a.py"}
    monkeypatch.setattr(gate, "git", lambda *a: answers.get(a[:1], ""))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    snapshots = iter([
        snap({"app": {}}, errors={"app.aislop": "npx failed"}),
        snap({"app": {"semgrep": {"scan_errors": 1}}}),
    ])  # fmt: skip
    monkeypatch.setattr(gate, "snapshot", lambda *a, **k: next(snapshots))
    assert gate.run_gate(settings, GateSettings(mode="block"), "origin/main").exit_code == 1
    text = next(settings.runs_dir.glob("*_gate_*/gate.md")).read_text()
    assert "app.aislop" in text and "base: app.semgrep" in text


def test_summary_caps_long_tables() -> None:
    many = [FINDING | {"blocking": False, "line": n} for n in range(gate.SHOWN + 5)]
    info = {"mode": "warn", "head": "h", "base": "b", "changed": 1, "targets": []}
    text = gate.summary(many, {}, info)
    assert "…and 5 more" in text and "**0 blocking**" in text


def test_diff_status_keeps_deletions_and_renames_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    status = (
        "M\tapps/app/app/a.py\nD\tapps/app/app/gone.py\nR100\tapps/app/app/old.py\tapps/app/app/new.py\nA\twiki/x.md"
    )
    monkeypatch.setattr(gate, "git", lambda *a: status)
    changed, touched, renames = gate.diff_status("base")
    assert changed == ["apps/app/app/a.py", "apps/app/app/new.py", "wiki/x.md"]
    assert "apps/app/app/gone.py" in touched and "apps/app/app/old.py" in touched
    assert renames == {"apps/app/app/old.py": "apps/app/app/new.py"}
    # a branch that only deletes an app file still measures the app
    assert "app" in gate.affected(TARGETS, ["apps/app/app/gone.py"])


def test_an_app_at_the_repo_root_is_touched_by_any_change() -> None:
    root_app = Target(name="acme", kind="python", root=".", source=("src",), tests=True)
    assert gate.affected((root_app, REPO), ["src/acme/pricing.py"]) == ["acme", "repo"]
    assert gate.lock_changed((root_app, REPO), ["uv.lock"]) == {"acme"}


def test_a_renamed_file_keeps_its_findings_old() -> None:
    base = snap({"app": aislop_items(item("apps/app/app/old.py", 5, "same"))})
    head = snap({"app": aislop_items(item("apps/app/app/new.py", 5, "same"))})
    changed = {"apps/app/app/new.py"}
    assert gate.new_compared(head, base, changed, {"apps/app/app/old.py": "apps/app/app/new.py"}) == []
    assert len(gate.new_compared(head, base, changed)) == 1  # without the rename map it would look new


def test_python_advisories_ratchet_on_their_total() -> None:
    def py(n: int) -> dict:
        return snap({"app": {"vulnerabilities": {"vulnerabilities": n}}})

    found = gate.ratchets(py(1), py(0), locks={"app"})
    assert [f["rule"] for f in found] == ["vulnerabilities.vulnerabilities"]


def test_gate_runs_keep_every_item(tmp_path: Path) -> None:
    from_report = Context(target=APP, checkout=tmp_path, raw_dir=tmp_path, settings=SETTINGS)
    from_gate = Context(target=APP, checkout=tmp_path, raw_dir=tmp_path, settings=SETTINGS, full=True)
    assert len(from_report.cap(list(range(10)), 3)) == 3
    assert len(from_gate.cap(list(range(10)), 3)) == 10


def test_a_new_app_ratchets_from_zero() -> None:
    base = snap({}, errors={"app": "not present at this commit"})
    found = gate.ratchets(counts(2, over={"a.py::f": 20}), base, locks=set())
    assert sorted(f["rule"] for f in found) == ["cognitive", "dependencies.issues", "suppressions.total"]


def test_a_renamed_file_keeps_its_cognitive_scores() -> None:
    base = gate.renamed(counts(0, over={"apps/app/app/old.py::f": 20}), {"apps/app/app/old.py": "apps/app/app/new.py"})
    assert gate.ratchets(counts(0, over={"apps/app/app/new.py::f": 20}), base, locks=set()) == []


def test_a_partial_semgrep_scan_is_a_tool_error() -> None:
    head = snap({"app": {"semgrep": {"scan_errors": 2}}, "ui": {"semgrep": {"scan_errors": 0}}})
    assert list(gate.partial_scans(head)) == ["app.semgrep"]


def test_a_deleted_lockfile_still_triggers_the_audit() -> None:
    assert gate.lock_changed(TARGETS, ["apps/app/uv.lock"]) == {"app"}


INTERROGATE = """
------------------------------ Detailed Coverage -------------------------------
| Name                                    |  Status |
|-----------------------------------------|---------|
| services/a.py (module)                  | COVERED |
| documented (L3)                         | COVERED |
| Old (L10)                               |  MISSED |
| Old.run (L12)                           |  MISSED |
| later (L30)                             | COVERED |
|-----------------------------------------|---------|
| services/b.py (module)                  |  MISSED |
| only (L4)                               |  MISSED |
"""


def test_missing_docstrings_carry_their_span() -> None:
    ends = {"apps/app/app/services/a.py": {3: (3, 5), 10: (10, 28), 12: (11, 20), 30: (30, 31)},
            "apps/app/app/services/b.py": {4: (4, 9)}}  # fmt: skip
    items = docstrings.missing(INTERROGATE, lambda name: f"apps/app/app/{name}", lambda file: ends[file])
    assert items == [
        {"file": "apps/app/app/services/a.py", "symbol": "Old", "line": 10, "start_line": 10, "end_line": 28},
        {"file": "apps/app/app/services/a.py", "symbol": "Old.run", "line": 12, "start_line": 11, "end_line": 20},
        {"file": "apps/app/app/services/b.py", "symbol": "(module)", "line": 1, "start_line": 1, "end_line": None},
        {"file": "apps/app/app/services/b.py", "symbol": "only", "line": 4, "start_line": 4, "end_line": 9},
    ]


def docstring_snap(*items: dict) -> dict:
    """A snapshot with the docstrings collector's per-symbol items for one app."""
    return snap({"app": {"docstrings": {"items": list(items), "worst_files": []}}})


def symbol(name: str, line: int, end: int | None, file: str = "apps/app/app/a.py") -> dict:
    """One symbol without a docstring."""
    return {"file": file, "symbol": name, "line": line, "end_line": end}


def test_docstrings_count_new_and_changed_symbols_only() -> None:
    base = docstring_snap(
        symbol("old_touched", 10, 20), symbol("old_untouched", 30, 40), symbol("(module)", 1, None),
        symbol("last_in_file", 70, 80),
    )  # fmt: skip
    head = docstring_snap(
        symbol("old_touched", 10, 20),  # the branch changed line 12, inside it
        symbol("old_untouched", 30, 40),  # never touched: not the branch's
        symbol("(module)", 1, None),  # an old module docstring gap: not the branch's
        symbol("brand_new", 50, 60),  # the branch added it
        symbol("last_in_file", 70, 80),  # an old function at the end of the file, changed on line 75
    )
    changed = {"apps/app/app/a.py"}
    found = gate.new_compared(head, base, changed, lines={"apps/app/app/a.py": {12, 55, 75}})
    assert sorted(f["symbol"] for f in found) == ["brand_new", "last_in_file", "old_touched"]


def test_the_coverage_count_is_not_a_gate_finding() -> None:
    per_file = {"file": "apps/app/app/a.py", "missing": 3, "percent": 50.0}
    head = snap({"app": {"docstrings": {"items": [], "worst_files": [per_file]}}})
    base = snap({"app": {"docstrings": {"items": [], "worst_files": [per_file | {"missing": 2}]}}})
    assert gate.new_compared(head, base, {"apps/app/app/a.py"}) == []


def test_docstrings_collector_lists_symbols_with_real_interrogate(tmp_path: Path) -> None:
    pkg = tmp_path / "apps" / "app" / "app"
    pkg.mkdir(parents=True)
    (pkg / "m.py").write_text('"""Module."""\n\n\ndef good():\n    """Doc."""\n\n\ndef bad():\n    return 1\n')
    ctx = Context(target=APP, checkout=tmp_path, raw_dir=tmp_path, settings=SETTINGS, full=True)
    result = docstrings.collect(ctx)
    assert [(i["file"], i["symbol"], i["line"]) for i in result["items"]] == [("apps/app/app/m.py", "bad", 8)]


def test_syntax_ends_stop_at_the_function_not_at_the_next_assignment(tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text(
        "def f():\n    return 1\n\n\nCONSTANT = 2\n\n\nclass C:\n    @staticmethod\n    def m():\n        pass\n"
    )
    assert docstrings.syntax_ends(src) == {1: (1, 2), 8: (8, 11), 10: (9, 11)}  # m's span starts at its decorator
    src.write_text("def broken(:\n")
    assert docstrings.syntax_ends(src) == {}


def test_diff_lines_split_head_and_base_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    diff = "--- a/a.py\n+++ b/a.py\n@@ -14,2 +13,0 @@ def f():\n-    x = 1\n-    y = 2\n@@ -20 +18,2 @@\n"
    monkeypatch.setattr(gate, "git", lambda *a: diff)
    assert gate.diff_lines("base") == ({"a.py": {18, 19}}, {"a.py": {14, 15, 20}})


def test_a_deletion_counts_only_inside_the_symbol_on_the_base() -> None:
    inside = symbol("decorated", 10, 20) | {"start_line": 9}  # its decorator was on line 9 of the base
    beside = symbol("beside", 30, 31)
    base = docstring_snap(inside, beside)
    head = docstring_snap(symbol("decorated", 9, 19), symbol("beside", 29, 30))  # one line shorter now
    found = gate.new_compared(head, base, {"apps/app/app/a.py"}, lines={}, removed={"apps/app/app/a.py": {9, 32}})
    # line 9 (the decorator) was inside "decorated"; line 32 was the blank line after "beside"
    assert [f["symbol"] for f in found] == ["decorated"]


def test_docstring_items_list_the_worst_files_first() -> None:
    items = [symbol("a1", 1, 2, "apps/app/app/a.py"), symbol("z1", 5, 6, "apps/app/app/z.py"),
             symbol("z2", 1, 2, "apps/app/app/z.py")]  # fmt: skip
    assert [i["symbol"] for i in docstrings.worst_first(items)] == ["z2", "z1", "a1"]
