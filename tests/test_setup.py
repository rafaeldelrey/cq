"""cq doctor, setup and init, and the fixes that made cq run outside angel-monitor."""

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from cq import doctor, init, proc
from cq.collectors import pyscn, scripts
from cq.config import DEFAULTS_DIR, PINS, Settings, Target, load_settings
from cq.context import Context

QLTY_LINE = f"qlty {PINS['qlty']} macos-arm64 (abc 2026-10-13)"


def fake_versions(monkeypatch: pytest.MonkeyPatch, found: dict[str, str]) -> None:
    """Pretend only the tools in ``found`` are installed, with those version lines."""
    monkeypatch.setattr(doctor, "version_line", lambda name, flag: found.get(name, ""))


ALL = {t.name: f"{t.name} 1.0" for t in doctor.TOOLS} | {"qlty": QLTY_LINE}


# ── doctor ────────────────────────────────────────────────────────────────────────────────────


def test_doctor_passes_when_everything_is_there(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_versions(monkeypatch, ALL)
    assert doctor.doctor() == 0
    assert "Everything cq needs is installed." in capsys.readouterr().out


def test_doctor_names_the_fix_for_each_missing_tool(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_versions(monkeypatch, {k: v for k, v in ALL.items() if k not in ("vale", "trivy")})
    assert doctor.doctor() == 1
    out = capsys.readouterr().out
    assert "brew install vale" in out
    assert "1 required tool(s)" in out  # trivy is optional: its collector skips


def test_doctor_holds_qlty_to_its_pin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake_versions(monkeypatch, ALL | {"qlty": "qlty 0.600.0 macos-arm64"})
    assert doctor.doctor() == 1
    out = capsys.readouterr().out
    assert f"cq pins {PINS['qlty']}" in out and "cq setup" in out


def test_version_line_reads_the_first_line_or_nothing() -> None:
    assert doctor.version_line("no-such-tool-anywhere", "--version") == ""
    assert doctor.version_line("git", "--version").startswith("git version")


# ── setup ─────────────────────────────────────────────────────────────────────────────────────


def test_setup_with_nothing_missing_installs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_versions(monkeypatch, ALL)
    assert doctor.setup(assume_yes=True) == 0
    assert "Nothing to install." in capsys.readouterr().out


def test_setup_shows_its_plan_and_stops_without_a_yes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_versions(monkeypatch, {k: v for k, v in ALL.items() if k not in ("vale", "qlty")})
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)  # no gh either
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a terminal
    assert doctor.setup() == 1
    out = capsys.readouterr().out
    assert "brew install vale gh" in out
    assert f"install qlty {PINS['qlty']}" in out
    assert "pass --yes" in out


def test_setup_installs_with_brew_then_qlty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_versions(monkeypatch, {k: v for k, v in ALL.items() if k not in ("vale", "qlty")})
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    brewed: list[list[str]] = []
    monkeypatch.setattr(doctor.subprocess, "run", lambda cmd, check: brewed.append(cmd))
    installed: list[str] = []
    monkeypatch.setattr(doctor, "install_qlty", lambda version: installed.append(version))
    monkeypatch.setattr(doctor, "doctor", lambda: 0)
    assert doctor.setup(assume_yes=True) == 0
    assert brewed == [["brew", "install", "vale"]]
    assert installed == [PINS["qlty"]]


def test_setup_without_homebrew_says_where_to_get_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_versions(monkeypatch, {k: v for k, v in ALL.items() if k != "vale"})
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.setup(assume_yes=True) == 1
    assert "https://brew.sh" in capsys.readouterr().out


def test_install_qlty_checks_before_it_copies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    triple = doctor.qlty_triple()
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path) -> str:
        calls.append(cmd)
        if cmd[:3] == ["gh", "release", "download"]:  # the release archive, as GitHub serves it
            binary = cwd / f"qlty-{triple}" / "qlty"
            binary.parent.mkdir()
            binary.write_text("#!/bin/sh\n")
            with tarfile.open(cwd / f"qlty-{triple}.tar.xz", "w:xz") as tar:
                tar.add(binary.parent, arcname=binary.parent.name)
            (binary.parent / "qlty").unlink()
            binary.parent.rmdir()
        return ""

    monkeypatch.setattr(doctor, "run", fake_run)
    dest = tmp_path / "bin" / "qlty"
    doctor.install_qlty("0.654.0", dest)
    assert [c[:3] for c in calls] == [
        ["gh", "release", "download"],
        ["shasum", "-a", "256"],
        ["gh", "attestation", "verify"],
    ]
    assert dest.read_text() == "#!/bin/sh\n"
    assert dest.stat().st_mode & 0o111


def test_confirm_reads_a_yes_from_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", Tty("y\n"))
    assert doctor.confirm("Go?", assume_yes=False) is True
    monkeypatch.setattr(sys, "stdin", Tty("\n"))
    assert doctor.confirm("Go?", assume_yes=False) is False


# ── init ──────────────────────────────────────────────────────────────────────────────────────


def test_init_adds_tool_cq_to_pyproject_and_qlty_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'acme'\n")
    assert init.init(load_settings(tmp_path), assume_yes=True) == 0
    settings = load_settings(tmp_path)
    assert settings.config_file == tmp_path / "pyproject.toml"
    assert [t.name for t in settings.targets] == ["acme", "repo"]  # still detected
    assert (tmp_path / ".qlty" / "qlty.toml").read_text() == (DEFAULTS_DIR / "qlty.toml").read_text()
    out = capsys.readouterr().out
    assert "/plugin install cq@cq" in out
    # A second init changes nothing.
    before = (tmp_path / "pyproject.toml").read_text()
    init.init(settings, assume_yes=True)
    assert (tmp_path / "pyproject.toml").read_text() == before
    assert "Settings already in pyproject.toml." in capsys.readouterr().out


def test_init_writes_cq_toml_when_there_is_no_pyproject(tmp_path: Path) -> None:
    init.add_settings(load_settings(tmp_path), assume_yes=True)
    text = (tmp_path / "cq.toml").read_text()
    assert "[gate]" in text and "[tool.cq" not in text
    assert load_settings(tmp_path).config_file == tmp_path / "cq.toml"


def test_init_asks_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    init.init(load_settings(tmp_path))
    assert not (tmp_path / "cq.toml").exists()
    assert not (tmp_path / ".qlty").exists()


# ── fixes for a repo that isn't angel-monitor ─────────────────────────────────────────────────


def test_dev_tools_as_an_extra_or_a_group(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n\n[dependency-groups]\ndev = ['pytest']\n")
    assert proc.uv_app(tmp_path, "pytest") == ["uv", "run", "-q", "pytest"]  # uv run includes the dev group
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n\n[project.optional-dependencies]\ndev = ['pytest']\n"
    )
    assert proc.uv_app(tmp_path, "pytest", overlay=("p==1",)) == [
        "uv",
        "run",
        "-q",
        "--extra",
        "dev",
        "--with",
        "p==1",
        "pytest",
    ]
    assert proc.has_dev_extra(tmp_path / "nowhere") is False


def test_own_tools_come_from_cqs_environment() -> None:
    assert proc.own("vulture") == str(Path(sys.executable).parent / "vulture")
    assert proc.own("no-such-tool") == "no-such-tool"


def test_pyscn_with_no_clones_counts_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = Target(name="a", kind="python", root=".", source=("src",), tests=False)
    (tmp_path / "src").mkdir()
    ctx = Context(target=target, checkout=tmp_path, raw_dir=tmp_path, settings=Settings(targets=(target,)))
    out = json.dumps({"summary": {"health_score": 97}, "clone": {"clone_pairs": None}})
    monkeypatch.setattr(pyscn, "run", lambda cmd, cwd: out)
    monkeypatch.setattr(pyscn, "require", lambda tool: None)
    result = pyscn.collect(ctx)
    assert (result["clone_pairs"], result["health_score"]) == (0, 97)


def test_shell_scripts_anywhere_for_a_root_shell_root(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel in ("run.sh", "deep/er/x.sh", "notes.txt"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("echo\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    target = Target(name="repo", kind="repo", root=".", source=(".",), tests=False)
    ctx = Context(
        target=target, checkout=tmp_path, raw_dir=tmp_path, settings=Settings(targets=(target,), shell_roots=(".",))
    )
    assert scripts.tracked_scripts(ctx) == ["deep/er/x.sh", "run.sh"]
