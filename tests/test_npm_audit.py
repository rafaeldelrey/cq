"""npm audit's error replies (#66): a tool error with npm's own message, and one retry for the registry's.

The registry errors are recorded npm output (invented values, the shape npm 10 prints); the local
error runs the real npm on an app with no lock.
"""

import json
import shutil
from pathlib import Path

import pytest

from cq.collectors import vulnerabilities
from cq.config import Settings, Target
from cq.context import Context
from cq.proc import ToolError

REGISTRY_ERROR = {
    "message": "400 Bad Request - POST https://registry.example.com/-/npm/v1/security/audits/quick - Bad Request",
    "method": "POST",
    "uri": "https://registry.example.com/-/npm/v1/security/audits/quick",
    "headers": {"set-cookie": ["session=abc123; HttpOnly; Secure"], "server": ["cdn"]},
    "statusCode": 400,
    "body": {"statusCode": 400, "error": "Bad Request", "message": "Invalid package tree, run npm install"},
    "error": {"summary": "", "detail": ""},
}
LOCAL_ERROR = {"error": {"code": "ENOLOCK", "summary": "This command requires an existing lockfile.", "detail": ""}}
REPORT = {
    "vulnerabilities": {
        "left-pad": {
            "severity": "high",
            "range": "<1.1.0",
            "via": [{"title": "Prototype pollution"}],
            "fixAvailable": True,
        }
    },
    "metadata": {"vulnerabilities": {"critical": 0, "high": 1, "moderate": 0, "low": 0, "info": 0, "total": 1}},
}


def ui_context(tmp_path: Path, lock: bool = True) -> Context:
    """A Context for a TypeScript app at ``tmp_path/ui``, with or without a package-lock.json."""
    app = tmp_path / "ui"
    app.mkdir()
    (app / "package.json").write_text('{"name": "demo-ui", "version": "1.0.0"}\n')
    if lock:
        (app / "package-lock.json").write_text('{"name": "demo-ui", "lockfileVersion": 3, "packages": {}}\n')
    raw = tmp_path / "raw"
    raw.mkdir()
    target = Target(name="ui", kind="typescript", root="ui", source=("src",), tests=False)
    return Context(target=target, checkout=tmp_path, raw_dir=raw, settings=Settings(targets=(target,)))


def npm_replies(monkeypatch: pytest.MonkeyPatch, *replies: dict) -> list[list[str]]:
    """Make each ``npm audit`` print the next reply, as npm does with exit 1; the commands are recorded."""
    commands: list[list[str]] = []
    queue = list(replies)

    def fake_run(cmd: list[str], cwd: Path, **_: object) -> str:
        commands.append(cmd)
        return json.dumps(queue.pop(0))

    monkeypatch.setattr(vulnerabilities, "run", fake_run)
    return commands


def test_a_registry_error_is_retried_once_and_the_retry_measures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = ui_context(tmp_path)
    commands = npm_replies(monkeypatch, REGISTRY_ERROR, REPORT)
    result = vulnerabilities.collect(ctx)
    assert len(commands) == 2
    assert (result["vulnerabilities"], result["high"], [i["package"] for i in result["items"]]) == (1, 1, ["left-pad"])
    assert json.loads((ctx.raw_dir / "npm-audit.json").read_text()) == REPORT  # the report kept, not the error


def test_a_registry_error_twice_is_a_tool_error_with_npm_s_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = npm_replies(monkeypatch, REGISTRY_ERROR, REGISTRY_ERROR)
    with pytest.raises(ToolError) as excinfo:
        vulnerabilities.collect(ui_context(tmp_path))
    assert len(commands) == 2
    assert str(excinfo.value) == "npm audit: 400 Invalid package tree, run npm install"
    assert "abc123" not in str(excinfo.value)


def test_a_registry_error_without_a_body_uses_its_top_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare = {"statusCode": 503, "message": "503 Service Unavailable"}
    npm_replies(monkeypatch, bare, bare)
    with pytest.raises(ToolError, match=r"^npm audit: 503 503 Service Unavailable$"):
        vulnerabilities.collect(ui_context(tmp_path))


def test_a_local_error_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = npm_replies(monkeypatch, LOCAL_ERROR)
    with pytest.raises(ToolError, match=r"^npm audit: ENOLOCK This command requires an existing lockfile\.$"):
        vulnerabilities.collect(ui_context(tmp_path))
    assert len(commands) == 1


def test_an_unknown_reply_is_a_tool_error_without_npm_s_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npm_replies(monkeypatch, {"headers": {"set-cookie": ["session=abc123"]}, "oops": True})
    with pytest.raises(ToolError) as excinfo:
        vulnerabilities.collect(ui_context(tmp_path))
    assert str(excinfo.value) == 'npm audit: {"oops": true}'


def test_a_report_measures_on_the_first_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = npm_replies(monkeypatch, REPORT)
    assert vulnerabilities.collect(ui_context(tmp_path))["vulnerable_packages"] == 1
    assert len(commands) == 1


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
def test_the_real_npm_without_a_lock_says_why(tmp_path: Path) -> None:
    """npm prints ENOLOCK with no network: a tool error with npm's words, not a KeyError."""
    with pytest.raises(ToolError, match=r"^npm audit: ENOLOCK This command requires an existing lockfile"):
        vulnerabilities.collect(ui_context(tmp_path, lock=False))
