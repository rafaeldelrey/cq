"""The LAN server: index, /latest/, hidden paths."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from server import serve


def make_run(root: Path, name: str, dashboard: bool = True, partial_run: bool = False) -> Path:
    """A run folder with a snapshot, findings and (optionally) a dashboard."""
    run = root / name
    run.mkdir(parents=True)
    (run / "snapshot.json").write_text(
        json.dumps({"short": "abc1234", "subject": "a commit", "errors": {}, "partial": partial_run})
    )
    (run / "findings.jsonl").write_text(
        json.dumps({"severity": "error", "new": True}) + "\n" + json.dumps({"severity": "info", "new": True}) + "\n"
    )
    if dashboard:
        (run / "dashboard.html").write_text("<p>dash</p>")
    return run


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    """The server on a free port over a temp runs/ folder."""
    make_run(tmp_path, "20261010T000000Z_aaa")
    make_run(tmp_path, "20261011T000000Z_bbb")
    make_run(tmp_path, "20261012T000000Z_ccc", dashboard=False)
    make_run(tmp_path, "20261013T000000Z_ddd", partial_run=True)
    (tmp_path / "_cache").mkdir()
    (tmp_path / "_cache" / "x.json").write_text("{}")
    (tmp_path / "20261011T000000Z_bbb" / ".coverage").write_text("db")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(serve.Handler, directory=str(tmp_path)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url: str) -> tuple[int, str, str]:
    """(status, final url, body)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - a local test server
            return resp.status, resp.url, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, url, ""


def test_latest_redirects_to_newest_run_with_a_dashboard(server: str) -> None:
    status, final, body = get(f"{server}/latest/")
    assert status == 200
    assert final.endswith("/20261011T000000Z_bbb/dashboard.html")
    assert body == "<p>dash</p>"


def test_index_lists_runs_newest_first(server: str) -> None:
    status, _, page = get(f"{server}/")
    assert status == 200
    body = page.split("<tbody>", 1)[1]
    assert body.index("20261012T000000Z_ccc") < body.index("20261011T000000Z_bbb") < body.index("20261010T000000Z_aaa")
    assert "_cache" not in body
    assert "20261013T000000Z_ddd" not in body  # partial: hidden by default
    assert "1 partial run(s) hidden" in page
    assert "20261013T000000Z_ddd" in get(f"{server}/?partial=1")[2]


@pytest.mark.parametrize("path", ["/_cache/x.json", "/20261011T000000Z_bbb/.coverage", "/%5Fcache/x.json"])
def test_hidden_paths_404(server: str, path: str) -> None:
    assert get(f"{server}{path}")[0] == 404


def test_summary_counts(tmp_path: Path) -> None:
    s = serve.summary(make_run(tmp_path, "r"))
    assert (s["short"], s["errors"], s["new"], s["tool_errors"]) == ("abc1234", 1, 1, 0)
