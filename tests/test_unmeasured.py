"""The "Not measured" hint: app folders below the root that detected targets leave out."""

import subprocess
from pathlib import Path

import pytest

from cq import cli, doctor, unmeasured
from cq.config import load_settings

TS_PACKAGE = '{"name": "web", "devDependencies": {"typescript": "5.6.3"}}\n'


def git(repo: Path, *args: str) -> None:
    """A git command in ``repo``, as a fixed test identity."""
    cmd = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args]
    subprocess.run(cmd, cwd=repo, check=True, capture_output=True)


def committed(root: Path, files: dict[str, str]) -> Path:
    """A git repo at ``root`` holding ``files``, all committed."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first")
    return root


MONOREPO = {
    "pyproject.toml": "[tool.ruff]\nline-length = 100\n",  # a root that only configures tools
    "apps/api/pyproject.toml": "[project]\nname = 'api'\nversion = '0.1.0'\n",
    "apps/api/app/__init__.py": '"""Api."""\n',
    "apps/web/package.json": TS_PACKAGE,
    "docs/package.json": '{"name": "docs-site", "dependencies": {"vitepress": "1.0.0"}}\n',  # not TypeScript
    "tests/fixtures/demo/package.json": TS_PACKAGE,  # test data, not an app
}


def test_init_and_run_name_the_app_folders_detection_leaves_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = committed(tmp_path / "mono", MONOREPO)
    expected = "Not measured: 2 app folder(s) below the root (apps/api, apps/web)."
    assert cli.main(["--repo", str(repo), "init", "--skills", "none"]) == 0
    assert expected in capsys.readouterr().out
    assert cli.main(["--repo", str(repo), "run", "--tool", "size", "--no-history"]) == 0
    assert expected in capsys.readouterr().err
    assert expected in (doctor.targets_line(repo) or "")


@pytest.mark.parametrize(
    "files",
    [
        # targets listed in the settings: the user chose them
        {**MONOREPO, "cq.toml": '[[targets]]\nname = "api"\nkind = "python"\nroot = "apps/api"\nsource = ["app"]\n'},
        # one app, at the root
        {"pyproject.toml": "[project]\nname = 'acme'\n", "acme/__init__.py": '"""Acme."""\n'},
        # manifests only under tests and fixtures
        {"tests/fixtures/demo/package.json": TS_PACKAGE, "test/sample/pyproject.toml": "[project]\nname = 'x'\n"},
    ],
    ids=["configured", "single-root", "fixtures-only"],
)
def test_no_hint_when_nothing_goes_unmeasured(tmp_path: Path, files: dict[str, str]) -> None:
    repo = committed(tmp_path / "r", files)
    assert unmeasured.hint(load_settings(repo)) is None
    assert doctor.targets_line(repo) is None


def test_a_long_list_is_cut_to_three_folders(tmp_path: Path) -> None:
    files = {f"services/s{i}/pyproject.toml": "[project]\nname = 's'\n" for i in range(5)}
    repo = committed(tmp_path / "r", files)
    line = unmeasured.hint(load_settings(repo)) or ""
    assert "5 app folder(s) below the root (services/s0, services/s1, services/s2 and 2 more)" in line


def test_only_tracked_manifests_count(tmp_path: Path) -> None:
    repo = committed(tmp_path / "r", {"README.md": "# r\n"})
    (repo / "apps" / "api").mkdir(parents=True)
    (repo / "apps" / "api" / "pyproject.toml").write_text("[project]\nname = 'api'\n")  # never added
    assert unmeasured.app_folders(repo) == []


def test_a_package_json_counts_with_a_tsconfig_and_not_when_unreadable(tmp_path: Path) -> None:
    repo = committed(
        tmp_path / "r",
        {
            "web/package.json": '{"name": "web"}\n',
            "web/tsconfig.json": "{}\n",
            "broken/package.json": "{not json",
        },
    )
    assert unmeasured.app_folders(repo) == ["web"]


def test_outside_a_git_repo_there_is_nothing_to_say(tmp_path: Path) -> None:
    (tmp_path / "apps" / "api").mkdir(parents=True)
    (tmp_path / "apps" / "api" / "pyproject.toml").write_text("[project]\nname = 'api'\n")
    assert unmeasured.app_folders(tmp_path) == []
    assert doctor.targets_line(tmp_path) is None


def test_doctor_stays_quiet_on_settings_it_cannot_read(tmp_path: Path) -> None:
    repo = committed(tmp_path / "r", {**MONOREPO, "cq.toml": "not = [valid toml"})
    assert doctor.targets_line(repo) is None


def test_doctor_prints_the_hint_for_its_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = committed(tmp_path / "mono", MONOREPO)
    doctor.doctor(repo)
    assert "Not measured: 2 app folder(s) below the root (apps/api, apps/web)." in capsys.readouterr().out
