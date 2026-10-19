"""cq's own tool config stays in step with the defaults it was copied from."""

from pathlib import Path

from cq.config import DEFAULTS_DIR

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
