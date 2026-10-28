"""The gate's docstring- and comment-only rule: a function or class whose syntax tree is the same on
both sides, docstrings aside, is unchanged, and its old findings don't come back as new (#79)."""

import subprocess
from pathlib import Path

import pytest

from cq import compare, diff, gate, qlty, symbols

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
        return {"a": "app/a.py:5", "b": "app/b.py:40", "at": at, "lines": 8, "similarity": 0.9, "type": 2}

    head = {"targets": {"app": {"pyscn": {"items": [clone("new-fingerprint")]}}}, "errors": {}}
    base = {"targets": {"app": {"pyscn": {"items": [clone("old-fingerprint")]}}}, "errors": {}}
    changed = {"app/a.py"}
    assert len(compare.new_compared(head, base, changed)) == 1  # by key alone it looks new
    assert compare.new_compared(head, base, changed, unchanged={"app/a.py": [(4, 12)]}) == []
    # a copy in changed code still counts
    assert len(compare.new_compared(head, base, changed, unchanged={"app/a.py": [(20, 30)]})) == 1
