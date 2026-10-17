"""Settings discovery, target detection and the layered tool configs."""

from pathlib import Path

import pytest

from cq import toolconfig
from cq.config import (
    DEFAULTS_DIR,
    REPO_TARGET,
    Target,
    detect_targets,
    ensure_data_dir,
    find_config,
    load_settings,
)


def write(path: Path, text: str = "") -> Path:
    """Write ``text`` to ``path``, creating its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ── where the settings come from ──────────────────────────────────────────────────────────────


def test_cq_toml_wins_over_pyproject(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[tool.cq]\ncomplexity_limit = 10\n")
    write(tmp_path / "cq.toml", "complexity_limit = 12\n")
    table, source = find_config(tmp_path)
    assert (table, source) == ({"complexity_limit": 12}, tmp_path / "cq.toml")


def test_pyproject_tool_cq_is_read_when_there_is_no_cq_toml(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\n\n[tool.cq]\ncognitive_limit = 9\n")
    assert load_settings(tmp_path).cognitive_limit == 9


def test_no_settings_at_all_detects_and_defaults(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\n")
    table, source = find_config(tmp_path)
    assert (table, source) == ({}, None)
    settings = load_settings(tmp_path)
    assert settings.config_file is None
    assert settings.data_dir == tmp_path.resolve() / ".cq"
    assert settings.shell_roots == (".",)


def test_an_explicit_config_file_is_used_as_given(tmp_path: Path) -> None:
    other = write(tmp_path / "conf" / "pyproject.toml", "[tool.cq]\ndata_dir = 'quality'\n")
    settings = load_settings(tmp_path, other)
    # Relative settings resolve from the settings file's folder, not the repo.
    assert settings.data_dir == (tmp_path / "conf" / "quality").resolve()
    plain = write(tmp_path / "conf" / "mine.toml", "complexity_limit = 7\n")
    assert load_settings(tmp_path, plain).complexity_limit == 7


def test_listed_targets_get_the_repo_target_added(tmp_path: Path) -> None:
    write(
        tmp_path / "cq.toml",
        '[[targets]]\nname = "api"\nkind = "python"\nroot = "apps/api"\nsource = ["app"]\ntests = true\n'
        'deptry_ignore = ["DEP002=uvicorn"]\n',
    )
    settings = load_settings(tmp_path)
    assert [t.name for t in settings.targets] == ["api", "repo"]
    assert settings.targets[0].deptry_ignore == ("DEP002=uvicorn",)


def test_docs_default_to_the_readme_and_docs_folder(tmp_path: Path) -> None:
    write(tmp_path / "README.md", "# x\n")
    assert load_settings(tmp_path).link_paths == ("README.md",)
    write(tmp_path / "docs" / "a.md")
    assert load_settings(tmp_path).link_paths == ("README.md", "docs")


# ── target detection ──────────────────────────────────────────────────────────────────────────


def test_detects_a_src_layout(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[project]\nname = 'Acme-Pricing'\n")
    write(tmp_path / "src" / "acme_pricing" / "__init__.py")
    write(tmp_path / "tests" / "test_x.py")
    app, repo = detect_targets(tmp_path)
    assert (app.name, app.kind, app.root, app.source, app.tests) == ("acme_pricing", "python", ".", ("src",), True)
    assert repo == REPO_TARGET


def test_detects_a_package_named_like_the_project(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[project]\nname = 'acme-pricing'\n")
    write(tmp_path / "acme_pricing" / "__init__.py")
    write(tmp_path / "other" / "__init__.py")
    assert detect_targets(tmp_path)[0].source == ("acme_pricing",)


def test_falls_back_to_top_level_packages_then_the_root(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[tool.ruff]\n")  # no [project] table: named after the folder
    write(tmp_path / "beta" / "__init__.py")
    write(tmp_path / "alpha" / "__init__.py")
    write(tmp_path / "tests" / "__init__.py")
    app = detect_targets(tmp_path)[0]
    assert app.source == ("alpha", "beta")
    assert app.name  # never empty
    for p in ("alpha", "beta", "tests"):
        (tmp_path / p / "__init__.py").unlink()
    assert detect_targets(tmp_path)[0].source == (".",)


def test_a_repo_without_pyproject_is_only_the_repo_target(tmp_path: Path) -> None:
    assert detect_targets(tmp_path) == (REPO_TARGET,)


def test_target_paths_for_an_app_at_the_root_and_in_a_folder() -> None:
    root_app = Target(name="a", kind="python", root=".", source=("src",), tests=False)
    sub_app = Target(name="b", kind="python", root="apps/b", source=("app",), tests=False)
    assert root_app.repo_path("uv.lock") == "uv.lock"
    assert sub_app.repo_path("uv.lock") == "apps/b/uv.lock"
    assert root_app.owns("anything/at/all.py")
    assert sub_app.owns("apps/b/app/x.py")
    assert not sub_app.owns("apps/bb/app/x.py")


# ── the data folder ───────────────────────────────────────────────────────────────────────────


def test_a_new_data_folder_ignores_itself_and_an_existing_one_is_left_alone(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\n")
    settings = load_settings(tmp_path)
    ensure_data_dir(settings)
    assert (settings.data_dir / ".gitignore").read_text().splitlines()[-1] == "*"
    existing = load_settings(tmp_path, write(tmp_path / "cq.toml", "data_dir = 'mine'\n"))
    (tmp_path / "mine").mkdir()
    ensure_data_dir(existing)
    assert not (tmp_path / "mine" / ".gitignore").exists()


# ── which config each tool reads ──────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with one app at apps/api."""
    write(tmp_path / "apps" / "api" / "pyproject.toml", "[project]\nname = 'api'\n")
    write(
        tmp_path / "cq.toml",
        '[[targets]]\nname = "api"\nkind = "python"\nroot = "apps/api"\nsource = ["app"]\n',
    )
    return tmp_path


def test_vulture_and_interrogate_prefer_the_app_then_the_root_then_cq(repo: Path) -> None:
    settings = load_settings(repo)
    api = settings.targets[0]
    assert toolconfig.vulture_config(settings, api) == DEFAULTS_DIR / "vulture.toml"
    assert toolconfig.interrogate_config(settings, api) == DEFAULTS_DIR / "interrogate.toml"
    root_pyproject = write(repo / "pyproject.toml", "[tool.vulture]\nmin_confidence = 80\n")
    assert toolconfig.vulture_config(settings, api) == root_pyproject
    write(repo / "apps" / "api" / "pyproject.toml", "[tool.vulture]\n[tool.interrogate]\n")
    assert toolconfig.vulture_config(settings, api) == repo / "apps" / "api" / "pyproject.toml"
    assert toolconfig.interrogate_config(settings, api) == repo / "apps" / "api" / "pyproject.toml"


def test_vulture_whitelist_is_picked_up(repo: Path) -> None:
    settings = load_settings(repo)
    assert toolconfig.vulture_whitelist(settings, settings.targets[0]) is None
    whitelist = write(repo / "apps" / "api" / "vulture_whitelist.py", "price\n")
    assert toolconfig.vulture_whitelist(settings, settings.targets[0]) == whitelist


def test_file_configs_go_named_then_repo_then_default(repo: Path) -> None:
    settings = load_settings(repo)
    assert toolconfig.cspell_config(settings) == DEFAULTS_DIR / "cspell.json"
    assert toolconfig.vale_config(settings) == DEFAULTS_DIR / "vale" / ".vale.ini"
    assert toolconfig.gitleaks_config(settings) == DEFAULTS_DIR / "gitleaks.toml"
    assert toolconfig.semgrep_rules(settings) == DEFAULTS_DIR / "semgrep"
    write(repo / "cspell.json", "{}")
    write(repo / ".vale.ini", "")
    write(repo / ".gitleaks.toml", "")
    (repo / ".semgrep").mkdir()
    settings = load_settings(repo)
    assert toolconfig.cspell_config(settings) == repo / "cspell.json"
    assert toolconfig.vale_config(settings) == repo / ".vale.ini"
    assert toolconfig.gitleaks_config(settings) == repo / ".gitleaks.toml"
    assert toolconfig.semgrep_rules(settings) == repo / ".semgrep"
    named = write(
        repo / "cq.toml",
        (repo / "cq.toml")
        .read_text()
        .replace("[[targets]]", 'cspell_config = "q/words.json"\nsemgrep_rules = "q/rules"\n\n[[targets]]', 1),
    )
    settings = load_settings(repo, named)
    assert toolconfig.cspell_config(settings) == (repo / "q" / "words.json").resolve()
    assert toolconfig.semgrep_rules(settings) == (repo / "q" / "rules").resolve()


def test_import_contracts_from_the_settings_folder_or_the_app(repo: Path) -> None:
    settings = load_settings(repo)
    api = settings.targets[0]
    assert toolconfig.import_contracts(settings, api) is None
    dotfile = write(repo / "apps" / "api" / ".importlinter", "[importlinter]\n")
    assert toolconfig.import_contracts(settings, api) == dotfile
    write(repo / "apps" / "api" / "pyproject.toml", "[tool.importlinter]\nroot_package = 'app'\n")
    assert toolconfig.import_contracts(settings, api) == repo / "apps" / "api" / "pyproject.toml"
    write(repo / "cq.toml", 'contracts_dir = "contracts"\n' + (repo / "cq.toml").read_text())
    settings = load_settings(repo)
    assert toolconfig.import_contracts(settings, api) is None  # a contracts_dir without this app's file
    ini = write(repo / "contracts" / "api.ini", "[importlinter]\n")
    assert toolconfig.import_contracts(settings, api) == ini.resolve()


def test_used_configs_label_their_source_and_change_with_the_file(repo: Path) -> None:
    settings = load_settings(repo)
    first = toolconfig.used_configs(settings)
    assert first["settings"]["file"] == "cq.toml"
    assert first["semgrep"]["file"] == "cq:semgrep"
    assert "qlty" not in first  # no .qlty/qlty.toml in the repo
    write(repo / "cspell.json", '{"words": ["a"]}')
    second = toolconfig.used_configs(load_settings(repo))
    assert second["cspell"]["file"] == "cspell.json"
    write(repo / "cspell.json", '{"words": ["b"]}')
    assert toolconfig.used_configs(load_settings(repo))["cspell"]["hash"] != second["cspell"]["hash"]
    outside = Path("/") / "elsewhere" / "x.toml"
    assert toolconfig.label(settings, outside) == "/elsewhere/x.toml"


def test_a_named_vulture_or_interrogate_file_wins_for_every_app(repo: Path) -> None:
    shared = write(repo / "quality" / "pyproject.toml", "[tool.vulture]\n[tool.interrogate]\n")
    write(repo / "apps" / "api" / "pyproject.toml", "[tool.vulture]\n")  # the named file still wins
    text = (repo / "cq.toml").read_text()
    write(
        repo / "cq.toml",
        'vulture_config = "quality/pyproject.toml"\ninterrogate_config = "quality/pyproject.toml"\n' + text,
    )
    settings = load_settings(repo)
    assert toolconfig.vulture_config(settings, settings.targets[0]) == shared.resolve()
    assert toolconfig.interrogate_config(settings, settings.targets[0]) == shared.resolve()


def test_the_default_vulture_ignores_framework_names() -> None:
    text = (DEFAULTS_DIR / "vulture.toml").read_text()
    assert '"handle_tool_error"' in text and '"@router.*"' in text
