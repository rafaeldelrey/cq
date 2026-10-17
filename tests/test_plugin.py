"""cq's Claude Code plugin: init's install question and doctor's line, with a fake `claude`."""

import io
import json
import sys
from pathlib import Path

import pytest

from cq import doctor, init, plugin
from cq.config import load_settings


class FakeClaude:
    """A `claude` CLI that records the commands it gets and answers `plugin list --json`."""

    def __init__(self, installed: list[dict] | None = None, fail: str = "") -> None:
        self.installed = installed or []
        self.fail = fail  # the subcommand that exits non-zero ("marketplace", "install")
        self.calls: list[list[str]] = []

    def capture(self, cmd: list[str], cwd: Path) -> str | None:
        assert cmd[1:] == ["plugin", "list", "--json"]
        return json.dumps(self.installed)

    def call(self, cmd: list[str], cwd: Path) -> int:
        self.calls.append(cmd[1:])
        return 1 if self.fail and self.fail in cmd else 0


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with one Python app."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'acme'\n")
    (tmp_path / "acme.py").write_text('"""Acme."""\n')
    return tmp_path


def use(monkeypatch: pytest.MonkeyPatch, fake: FakeClaude) -> FakeClaude:
    """Put the fake claude in place of the real one."""
    monkeypatch.setattr(plugin, "claude_cli", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(plugin, "capture", fake.capture)
    monkeypatch.setattr(plugin, "call", fake.call)
    return fake


def tty(answer: str) -> io.StringIO:
    """A stdin that is a terminal and answers ``answer``."""

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    return Tty(answer + "\n")


MARKETPLACE_ADD = ["plugin", "marketplace", "add", "rafaeldelrey/cq", "--scope"]
INSTALL = ["plugin", "install", "cq@cq", "--scope"]


@pytest.mark.parametrize(("answer", "scope"), [("u", "user"), ("p", "project")])
def test_the_answer_installs_marketplace_then_plugin_at_that_scope(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: str, scope: str
) -> None:
    fake = use(monkeypatch, FakeClaude())
    monkeypatch.setattr(sys, "stdin", tty(answer))
    init.add_plugin(load_settings(repo), skills=None, assume_yes=False)
    assert fake.calls == [[*MARKETPLACE_ADD, scope], [*INSTALL, scope]]
    out = capsys.readouterr().out
    assert "runs: claude plugin marketplace add rafaeldelrey/cq --scope project && claude plugin install" in out
    assert ("commit .claude/settings.json" in out) == (scope == "project")


@pytest.mark.parametrize("answer", ["s", "", "nonsense"])
def test_skip_or_no_answer_installs_nothing(repo: Path, monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    fake = use(monkeypatch, FakeClaude())
    monkeypatch.setattr(sys, "stdin", tty(answer))
    init.add_plugin(load_settings(repo), skills=None, assume_yes=False)
    assert fake.calls == []


def test_without_a_terminal_it_skips_and_says_how(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = use(monkeypatch, FakeClaude())
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    init.add_plugin(load_settings(repo), skills=None, assume_yes=False)
    assert fake.calls == []
    assert "pass --skills user|project|none" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("skills", "assume_yes", "scope"), [("project", False, "project"), ("user", True, "user"), (None, True, "user")]
)
def test_skills_or_yes_answer_without_asking(
    repo: Path, monkeypatch: pytest.MonkeyPatch, skills: str | None, assume_yes: bool, scope: str
) -> None:
    fake = use(monkeypatch, FakeClaude())
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # nobody to ask: the flags decide
    init.add_plugin(load_settings(repo), skills=skills, assume_yes=assume_yes)
    assert fake.calls == [[*MARKETPLACE_ADD, scope], [*INSTALL, scope]]


def test_skills_none_installs_nothing_even_with_yes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, FakeClaude())
    init.add_plugin(load_settings(repo), skills="none", assume_yes=True)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("row", "said"),
    [
        ({"id": "cq@cq", "scope": "user", "version": "0.2.0"}, "already installed for you"),
        (
            {"id": "cq@cq", "scope": "project", "version": "0.2.0", "projectPath": "{repo}"},
            "already installed for this repo",
        ),
    ],
)
def test_an_installed_plugin_is_not_offered_again(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], row: dict, said: str
) -> None:
    row = {k: (str(repo) if v == "{repo}" else v) for k, v in row.items()}
    fake = use(monkeypatch, FakeClaude(installed=[row]))
    init.add_plugin(load_settings(repo), skills="user", assume_yes=True)
    assert fake.calls == []
    assert said in capsys.readouterr().out


def test_installed_for_another_repo_is_still_offered(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    other = {"id": "cq@cq", "scope": "project", "version": "0.2.0", "projectPath": str(tmp_path / "elsewhere")}
    unrelated = {"id": "other@market", "scope": "user", "version": "1.0"}
    fake = use(monkeypatch, FakeClaude(installed=[other, unrelated]))
    init.add_plugin(load_settings(repo), skills="project", assume_yes=False)
    assert fake.calls[-1] == [*INSTALL, "project"]


def test_without_claude_it_prints_the_lines_to_paste(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    init.add_plugin(load_settings(repo), skills="user", assume_yes=True)  # conftest: no claude on this machine
    out = capsys.readouterr().out
    assert "Claude Code isn't on the PATH" in out and "/plugin install cq@cq" in out


def test_a_failed_marketplace_add_stops_before_the_install(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = use(monkeypatch, FakeClaude(fail="marketplace"))
    assert init.init(load_settings(repo), assume_yes=True, skills="user") == 0  # init still succeeds
    assert fake.calls == [[*MARKETPLACE_ADD, "user"]]
    assert "The plugin wasn't installed" in capsys.readouterr().out


def test_a_list_claude_cannot_answer_counts_as_not_installed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, FakeClaude())
    monkeypatch.setattr(plugin, "capture", lambda cmd, cwd: None)  # claude failed or hung
    assert plugin.installed("claude", repo) == {}
    monkeypatch.setattr(plugin, "capture", lambda cmd, cwd: "not json")
    assert plugin.installed("claude", repo) == {}
    monkeypatch.setattr(plugin, "capture", lambda cmd, cwd: '{"unexpected": "shape"}')
    assert plugin.installed("claude", repo) == {}
    assert fake.calls == []


@pytest.mark.parametrize(
    ("claude", "installed", "line"),
    [
        (False, [], "--  claude plugin  Claude Code not installed"),
        (True, [], "--  claude plugin  not installed\n      for /code-quality and /setup-cq: cq init"),
        (True, [{"id": "cq@cq", "scope": "user", "version": "0.2.0"}], "ok  claude plugin  cq@cq 0.2.0 (user)"),
    ],
)
def test_doctor_reports_the_plugin_and_never_fails_on_it(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    claude: bool,
    installed: list,
    line: str,
) -> None:
    if claude:
        use(monkeypatch, FakeClaude(installed=installed))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        doctor, "version_line", lambda name, flag: f"qlty {doctor.PINS['qlty']}" if name == "qlty" else "1.0"
    )
    assert doctor.doctor() == 0  # the plugin is optional
    assert line in capsys.readouterr().out


def test_the_real_helpers_run_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the conftest guard off: these run harmless commands, never claude
    assert plugin.capture(["echo", "hi"], tmp_path) == "hi\n"
    assert plugin.capture(["false"], tmp_path) is None
    assert plugin.call(["true"], tmp_path) == 0
