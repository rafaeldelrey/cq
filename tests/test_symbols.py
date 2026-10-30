"""The gate's docstring- and comment-only rule: a function or class whose syntax tree is the same on
both sides, docstrings aside, is unchanged, and its old findings don't come back as new (#79)."""

import subprocess
from pathlib import Path

import pytest

from cq import compare, diff, gate, qlty, symbols
from cq.proc import ToolError

BASE = '''\
import os


def send(to, text):
    """Send it."""
    if not to:
        return None
    return os.path.join(to, text)  # the address


class Sender:
    def run(self):
        return send("a", "b")


@staticmethod
def helper():
    return 1
'''


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    """Write the files and commit them."""
    for name, text in files.items():
        (repo / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message],
                   cwd=repo, check=True)  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo whose first commit is BASE in a.py, as the current directory (the gate's)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    commit(tmp_path, {"a.py": BASE}, "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def base_sha(repo: Path) -> str:
    """The first commit."""
    return subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()  # fmt: skip


def branch(repo: Path, files: dict[str, str]) -> tuple[str, dict[str, list[symbols.Span]], dict[str, set[int]]]:
    """Commit ``files`` on top of the base: (the base sha, the unchanged spans, the changed lines)."""
    sha = base_sha(repo)
    commit(repo, files, "branch")
    changed, _, renames = diff.diff_status(sha)
    return sha, diff.unchanged_code(sha, changed, renames), diff.changed_lines(sha)


# ── the tree comparison ───────────────────────────────────────────────────────────────────────


def test_a_docstring_only_edit_leaves_the_function_unchanged(repo: Path) -> None:
    _, unchanged, lines = branch(repo, {"a.py": BASE.replace('"""Send it."""', '"""Send the text to them."""')})
    assert symbols.outside(lines, unchanged) == {}  # the one changed line sits in an unchanged symbol
    assert (4, 8) in unchanged["a.py"]


def test_an_added_docstring_and_a_comment_leave_symbols_unchanged(repo: Path) -> None:
    edited = BASE.replace("    def run(self):\n", '    def run(self):\n        """Run it."""\n').replace(
        "# the address", "# the address, joined"
    )
    _, unchanged, lines = branch(repo, {"a.py": edited})
    assert symbols.outside(lines, unchanged) == {}


def test_a_code_edit_changes_the_function_and_the_class_around_it(repo: Path) -> None:
    edited = BASE.replace('return send("a", "b")', 'return send("a", "c")')
    _, unchanged, lines = branch(repo, {"a.py": edited})
    assert symbols.outside(lines, unchanged) == {"a.py": {13}}
    assert (4, 8) in unchanged["a.py"]  # send, untouched, is still unchanged


def test_a_new_function_and_a_new_file_count_as_changed(repo: Path) -> None:
    added = BASE + '\n\ndef extra():\n    """New."""\n    return 2\n'
    _, unchanged, lines = branch(repo, {"a.py": added, "b.py": "def b():\n    return 1\n"})
    assert symbols.outside(lines, unchanged) == {"a.py": {19, 20, 21, 22, 23}, "b.py": {1, 2}}
    assert "b.py" not in unchanged


def test_a_renamed_file_is_compared_with_its_source(repo: Path) -> None:
    sha = base_sha(repo)
    subprocess.run(["git", "mv", "a.py", "moved.py"], cwd=repo, check=True)
    commit(repo, {"moved.py": BASE.replace('"""Send it."""', '"""Send it now."""')}, "rename")
    changed, _, renames = diff.diff_status(sha)
    unchanged = diff.unchanged_code(sha, changed, renames)
    assert renames == {"a.py": "moved.py"}
    assert symbols.outside(diff.changed_lines(sha), unchanged) == {}


def test_a_file_that_does_not_parse_keeps_the_line_rule(repo: Path) -> None:
    _, unchanged, lines = branch(repo, {"a.py": BASE.replace("def run(self):", "def run(self:")})
    assert unchanged == {}
    assert symbols.outside(lines, unchanged) == lines


LATIN1 = '# -*- coding: latin-1 -*-\n\n\ndef greet():\n    """Say it."""\n    return "olá"\n'.encode("latin-1")


def test_a_source_in_its_declared_encoding_is_compared(repo: Path) -> None:
    (repo / "l.py").write_bytes(LATIN1)
    commit(repo, {}, "latin-1 file")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                         text=True).stdout.strip()  # fmt: skip
    (repo / "l.py").write_bytes(LATIN1.replace(b"Say it.", b"Say it in Portuguese."))
    commit(repo, {}, "docstring")
    changed, _, renames = diff.diff_status(sha)
    assert diff.unchanged_code(sha, changed, renames) == {"l.py": [(4, 6)]}


def test_a_source_that_does_not_decode_keeps_the_line_rule(repo: Path) -> None:
    undecodable = b'def f():\n    """Doc."""\n    return "\xff\xfe"\n'  # no encoding declared, not UTF-8
    (repo / "u.py").write_bytes(undecodable)
    commit(repo, {}, "undecodable file")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                         text=True).stdout.strip()  # fmt: skip
    (repo / "u.py").write_bytes(undecodable.replace(b"Doc.", b"Docs."))
    commit(repo, {}, "docstring")
    changed, _, renames = diff.diff_status(sha)
    assert diff.unchanged_code(sha, changed, renames) == {}  # no tree to compare: the line rule stands


def test_a_missing_file_is_a_tool_error(repo: Path) -> None:
    with pytest.raises(ToolError, match=r"git show HEAD:nope\.py exited"):
        diff.git_text("HEAD", "nope.py")


def test_a_name_defined_twice_is_never_unchanged() -> None:
    source = "def f():\n    return 1\n\n\ndef f():\n    return 2\n"
    assert symbols.unchanged_spans(source, source) == []
    assert symbols.symbols("def f(:\n") is None


def test_decorators_are_part_of_the_span() -> None:
    assert symbols.symbols(BASE)["helper"][0] == (16, 18)  # type: ignore[index]


# ── the gate ──────────────────────────────────────────────────────────────────────────────────


def test_a_docstring_edit_does_not_make_the_functions_old_smells_new(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, unchanged, _ = branch(repo, {"a.py": BASE.replace('"""Send it."""', '"""Send the text."""')})
    smell = {"plugin": "qlty", "severity": "warning", "msg": "m", "properties": {}, "rule": "return-statements",
             "file": "a.py", "line": 4, "end_line": 8}  # fmt: skip
    monkeypatch.setattr(qlty, "run_sarif", lambda ctx, args, name: [smell] if name == "gate-smells" else [])
    assert gate.line_level(None, sha, unchanged) == []  # type: ignore[arg-type]
    assert len(gate.line_level(None, sha)) == 1  # type: ignore[arg-type]  # without the rule: the docstring line overlaps


def test_a_clone_in_unchanged_code_is_old_even_with_a_new_key() -> None:
    def clone(at: str) -> dict:
        return {"a": "app/a.py:5", "b": "app/b.py:40", "at": at, "lines": 8, "b_lines": 8, "similarity": 0.9,
                "type": 2}  # fmt: skip

    head = {"targets": {"app": {"pyscn": {"items": [clone("new-fingerprint")]}}}, "errors": {}}
    base = {"targets": {"app": {"pyscn": {"items": [clone("old-fingerprint")]}}}, "errors": {}}
    changed = {"app/a.py"}
    assert len(compare.new_compared(head, base, changed)) == 1  # by key alone it looks new
    assert compare.new_compared(head, base, changed, unchanged={"app/a.py": [(4, 12)]}) == []
    # a copy in changed code still counts
    assert len(compare.new_compared(head, base, changed, unchanged={"app/a.py": [(20, 30)]})) == 1


def test_a_clone_that_runs_past_the_unchanged_symbol_is_new() -> None:
    clone = {"a": "app/a.py:5", "b": "app/b.py:40", "at": "x", "lines": 12, "b_lines": 12, "similarity": 0.9,
             "type": 2}  # fmt: skip
    head = {"targets": {"app": {"pyscn": {"items": [clone]}}}, "errors": {}}
    base = {"targets": {"app": {"pyscn": {"items": []}}}, "errors": {}}
    # it starts in the unchanged symbol (4, 8) but its lines 9 to 16 are outside it
    assert len(compare.new_compared(head, base, {"app/a.py"}, unchanged={"app/a.py": [(4, 8)]})) == 1
    assert compare.new_compared(head, base, {"app/a.py"}, unchanged={"app/a.py": [(4, 20)]}) == []


def test_a_clone_without_both_lengths_is_never_set_aside() -> None:
    clone = {"a": "app/a.py:5", "b": "app/b.py:40", "at": "x", "lines": 8, "similarity": 0.9, "type": 2}
    head = {"targets": {"app": {"pyscn": {"items": [clone]}}}, "errors": {}}  # an older snapshot: no b_lines
    base = {"targets": {"app": {"pyscn": {"items": []}}}, "errors": {}}
    assert len(compare.new_compared(head, base, {"app/a.py"}, unchanged={"app/a.py": [(1, 50)]})) == 1


def test_a_clone_path_with_spaces_is_read_whole() -> None:
    clone = {"a": "my app/a.py:5", "b": "my app/b.py:40", "at": "x", "lines": 8, "b_lines": 8, "similarity": 0.9,
             "type": 2}  # fmt: skip
    head = {"targets": {"app": {"pyscn": {"items": [clone]}}}, "errors": {}}
    base = {"targets": {"app": {"pyscn": {"items": []}}}, "errors": {}}
    changed = {"my app/a.py", "my app/b.py"}
    assert len(compare.new_compared(head, base, changed)) == 1  # not mistaken for untouched "app/a.py"
    unchanged = {"my app/a.py": [(1, 13)], "my app/b.py": [(35, 50)]}
    assert compare.new_compared(head, base, changed, unchanged=unchanged) == []
