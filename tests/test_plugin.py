"""cq's Claude Code plugin: init's install question and doctor's line, with a fake `claude`."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cq import cli, doctor, init, plugin
from cq.config import load_settings


class FakeClaude:
    """A `claude` CLI that records the commands it gets and answers `plugin list --json`."""

    def __init__(self, installed: list[dict] | None = None, fail: str = "") -> None:
        self.installed = installed or []
        self.fail = fail  # the subcommand that exits non-zero ("marketplace", "install")
        self.calls: list[list[str]] = []

    def capture(self, cmd: list[str], cwd: Path) -> str | None:
        if cmd[0] == "git":  # doctor finding the repo root: the folder is the root here
            return None
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
    monkeypatch.setattr(
        plugin, "capture", lambda cmd, cwd: '["a string row", {"id": "cq@cq", "scope": "user", "version": "1"}]'
    )
    assert plugin.installed("claude", repo) == {"user": "1"}  # odd rows skipped, real ones kept
    assert fake.calls == []


@pytest.mark.parametrize(
    ("claude", "installed", "line"),
    [
        (False, [], "--  claude plugin  Claude Code not installed"),
        (
            True,
            [],
            "--  claude plugin  not installed\n      for /cq, /setup-cq and /improve-code-quality: cq init",
        ),
        (True, [{"id": "cq@cq", "scope": "user", "version": "0.2.0"}], "ok  claude plugin  cq@cq (user 0.2.0)"),
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


def test_skills_project_installs_for_the_team_when_only_the_user_has_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # /setup-cq's case: the plugin is running for the user, and the team should get it too.
    fake = use(monkeypatch, FakeClaude(installed=[{"id": "cq@cq", "scope": "user", "version": "0.2.0"}]))
    init.add_plugin(load_settings(repo), skills="project", assume_yes=True)
    assert fake.calls == [[*MARKETPLACE_ADD, "project"], [*INSTALL, "project"]]
    assert "commit .claude/settings.json" in capsys.readouterr().out


def test_an_unrecognised_answer_says_it_skipped(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = use(monkeypatch, FakeClaude())
    monkeypatch.setattr(sys, "stdin", tty("x"))
    init.add_plugin(load_settings(repo), skills=None, assume_yes=False)
    assert fake.calls == []
    assert "x: not u, p or s, so skipped." in capsys.readouterr().out


def test_doctor_checks_the_repo_root_from_a_subfolder(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sub = repo / "deep" / "er"
    sub.mkdir(parents=True)
    row = {"id": "cq@cq", "scope": "project", "version": "0.2.0", "projectPath": str(repo)}
    fake = use(monkeypatch, FakeClaude(installed=[row]))
    monkeypatch.setattr(plugin, "repo_root_of", lambda folder: repo)
    assert plugin.status(sub) == (True, "cq@cq (project 0.2.0)", "")
    assert fake.calls == []


def test_repo_root_of_asks_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the conftest guard off: this runs git, never claude
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert plugin.repo_root_of(sub) == sub  # not a repo: the folder itself
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert plugin.repo_root_of(sub).resolve() == tmp_path.resolve()


def test_a_claude_that_hangs_counts_as_failed_or_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the conftest guard off: subprocess itself is faked here, nothing runs

    def hang(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(plugin.subprocess, "run", hang)
    assert plugin.capture(["claude", "plugin", "list", "--json"], tmp_path) is None
    assert plugin.call(["claude", "plugin", "install", "cq@cq"], tmp_path) == 1


def test_cq_doctor_reports_the_plugin_for_the_repo_given(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(plugin, "status", lambda folder: seen.append(folder) or (False, "not installed", "cq init"))
    monkeypatch.setattr(
        doctor, "version_line", lambda name, flag: f"qlty {doctor.PINS['qlty']}" if name == "qlty" else "1.0"
    )
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--repo", str(repo), "doctor"]) == 0
    assert seen == [repo.resolve()]


def test_a_missing_git_leaves_the_folder_as_the_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the conftest guard off: subprocess itself is faked here, nothing runs

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(plugin.subprocess, "run", missing)
    assert plugin.capture(["git", "rev-parse", "--show-toplevel"], tmp_path) is None
    assert plugin.repo_root_of(tmp_path) == tmp_path


def test_skills_project_installs_over_a_local_install(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # local scope is settings.local.json, not committed: the team still doesn't have it.
    local = {"id": "cq@cq", "scope": "local", "version": "0.2.0", "projectPath": str(repo)}
    fake = use(monkeypatch, FakeClaude(installed=[local]))
    init.add_plugin(load_settings(repo), skills="project", assume_yes=False)
    assert fake.calls == [[*MARKETPLACE_ADD, "project"], [*INSTALL, "project"]]
    fake.calls.clear()
    init.add_plugin(load_settings(repo), skills=None, assume_yes=True)  # no explicit project: already here
    assert fake.calls == []


def test_doctor_says_a_disabled_plugin_is_not_ready(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    off = {"id": "cq@cq", "scope": "user", "version": "0.2.0", "enabled": False}
    use(monkeypatch, FakeClaude(installed=[off]))
    assert plugin.status(repo) == (
        False,
        "cq@cq (user 0.2.0), disabled",
        "claude plugin enable cq@cq",  # claude finds the settings file that disables it
    )
    on_here = {"id": "cq@cq", "scope": "project", "version": "0.2.0", "projectPath": str(repo), "enabled": True}
    use(monkeypatch, FakeClaude(installed=[off, on_here]))
    assert plugin.status(repo)[0] is True  # enabled for this repo: the skills work here


def test_doctor_points_out_a_missing_qlty_gitignore(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        doctor, "version_line", lambda name, flag: f"qlty {doctor.PINS['qlty']}" if name == "qlty" else "1.0"
    )
    assert doctor.doctor() == 0
    assert ".qlty/.gitignore" not in capsys.readouterr().out  # no qlty config here: nothing to say
    (repo / ".qlty").mkdir()
    (repo / ".qlty" / "qlty.toml").write_text('config_version = "0"\n')
    assert doctor.doctor() == 0  # a note, never a failure
    out = capsys.readouterr().out
    assert "--  .qlty/.gitignore  missing in this repo" in out
    assert "`cq init` adds it" in out
    (repo / ".qlty" / ".gitignore").write_text("*\n")
    assert doctor.doctor() == 0
    assert ".qlty/.gitignore" not in capsys.readouterr().out
    assert (repo / ".qlty" / ".gitignore").read_text() == "*\n"  # doctor changed nothing


def test_init_names_every_skill_the_plugin_brings(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, FakeClaude())
    monkeypatch.setattr(sys, "stdin", tty("u"))
    init.add_plugin(load_settings(repo), skills=None, assume_yes=False)
    out = capsys.readouterr().out
    named = "/cq, /setup-cq and /improve-code-quality"
    assert f"Install the cq skills for Claude Code ({named})?" in out
    assert f"Installed for you: {named} work in every repo." in out


def test_the_skill_list_is_the_plugins() -> None:
    """A skill added to plugin.json without cq naming it fails here."""
    manifest = json.loads((Path(__file__).parents[1] / ".claude-plugin" / "plugin.json").read_text())
    assert [f"/{Path(s).name}" for s in manifest["skills"]] == list(plugin.SKILLS)
