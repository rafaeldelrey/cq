"""cq's own files stay in step: its tool config with the defaults, its install lines with its version."""

import re
import tomllib
from pathlib import Path

from cq.config import DEFAULTS_DIR, QLTY_IGNORE_DEFAULT

ROOT = Path(__file__).resolve().parent.parent
CLI_MODULES = '"cq/cli.py", "cq/init.py", "cq/doctor.py", "cq/complexity_gate.py"'


def test_qlty_config_is_cqs_default() -> None:
    assert (ROOT / ".qlty" / "qlty.toml").read_text() == (DEFAULTS_DIR / "qlty.toml").read_text()


def test_semgrep_rules_differ_from_the_defaults_only_in_the_cli_exclusion() -> None:
    assert (ROOT / ".semgrep" / "tests" / "cq-tests.yml").read_text() == (
        DEFAULTS_DIR / "semgrep" / "tests" / "cq-tests.yml"
    ).read_text()
    ours = (ROOT / ".semgrep" / "cq.yml").read_text().splitlines()
    default = (DEFAULTS_DIR / "semgrep" / "cq.yml").read_text().splitlines()
    rules_from = next(i for i, line in enumerate(default) if line.startswith("rules:"))
    ours_from = next(i for i, line in enumerate(ours) if line.startswith("rules:"))
    changed = [(a, b) for a, b in zip(default[rules_from:], ours[ours_from:], strict=True) if a != b]
    assert changed == [
        (
            '      exclude: ["**/tests/**", "**/__main__.py"]',
            f'      exclude: ["**/tests/**", "**/__main__.py", {CLI_MODULES}]',
        )
    ]


def test_every_install_line_names_this_version() -> None:
    """The README and /setup-cq pin the tag users install; a release bumps them with the version."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    docs = [ROOT / "README.md", *sorted((ROOT / "skills").rglob("SKILL.md"))]
    pins = {
        (doc.relative_to(ROOT).as_posix(), tag)
        for doc in docs
        for tag in re.findall(r"rafaeldelrey/cq(?:\.git)?@([\w.-]*\w)", doc.read_text())
    }
    assert pins, "no pinned install line found"
    assert {tag for _, tag in pins} == {f"v{version}"}, sorted(pins)


def test_cqs_qlty_gitignore_is_the_one_cq_init_writes() -> None:
    assert (ROOT / ".qlty" / ".gitignore").read_text() == (DEFAULTS_DIR / QLTY_IGNORE_DEFAULT).read_text()
