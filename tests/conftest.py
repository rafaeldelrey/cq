"""Shared test setup."""

from collections.abc import Callable
from pathlib import Path

import pytest

from cq import diff, gate, plugin


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the machine's real `claude`: an install would change the user's Claude config.

    Claude Code looks absent, and running a command through the plugin module fails the test; a test
    that wants claude sets its own fakes on top.
    """

    def refuse(cmd: list[str], cwd: Path) -> object:
        raise AssertionError(f"a test tried to run the real {cmd[0]}: {cmd}")

    monkeypatch.setattr(plugin, "claude_cli", lambda: None)
    monkeypatch.setattr(plugin, "capture", refuse)
    monkeypatch.setattr(plugin, "call", refuse)


@pytest.fixture
def use_git(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., str]], None]:
    """Answer every git call the gate makes (in cq.gate and cq.diff) with a fake."""

    def install(fake: Callable[..., str]) -> None:
        monkeypatch.setattr(gate, "git", fake)
        monkeypatch.setattr(diff, "git", fake)

    return install
