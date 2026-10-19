"""The plugin's manifest and its skills agree: every skill folder is listed, and each SKILL.md is well formed."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())


def frontmatter(skill: Path) -> dict[str, str]:
    """A SKILL.md's frontmatter fields (``key: value`` lines between the ``---`` fences)."""
    match = re.match(r"^---\n(.*?)\n---\n", skill.read_text(), re.DOTALL)
    assert match, f"{skill} has no frontmatter"
    return dict(re.findall(r"^([\w-]+):\s*(.*)$", match[1], re.MULTILINE))


def test_every_skill_folder_is_listed_and_every_listed_skill_exists() -> None:
    listed = {(ROOT / path).resolve() for path in MANIFEST["skills"]}
    folders = {p.parent.resolve() for p in (ROOT / "skills").glob("*/SKILL.md")}
    assert listed == folders


def test_each_skill_is_named_after_its_folder_and_described() -> None:
    for path in MANIFEST["skills"]:
        fields = frontmatter(ROOT / path / "SKILL.md")
        assert fields.get("name") == Path(path).name
        assert fields.get("description")


def test_each_skill_links_only_files_it_has() -> None:
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        for target in re.findall(r"\]\(((?:references|scripts)/[^)#]+)\)", skill.read_text()):
            assert (skill.parent / target).exists(), f"{skill}: {target}"


def test_the_plugin_version_is_the_package_version() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert MANIFEST["version"] == version
