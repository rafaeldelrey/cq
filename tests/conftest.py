"""Shared test setup."""

from pathlib import Path

import pytest

from cq import plugin


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
