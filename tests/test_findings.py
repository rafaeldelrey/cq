"""The unified findings, and the parsing in the newer collectors."""

from pathlib import Path

from cq import findings
from cq.collectors import imports, mutation, semgrep
from cq.collectors.dependencies import first_party_packages
from cq.config import Settings, Target
from cq.context import Context


def snapshot(targets: dict, errors: dict | None = None) -> dict:
    """The parts of a snapshot findings.py reads."""
    return {"targets": targets, "errors": errors or {}}


def test_from_snapshot_normalises_and_orders_by_severity() -> None:
    snap = snapshot(
        {
            "app": {
                "complexity": {
                    "limit": 15,
                    "worst": [
                        {"function": "a.py::f", "cc": 20, "rank": "C"},
                        {"function": "a.py::g", "cc": 3, "rank": "A"},
                    ],
                },
                "pyright": {"items": [{"file": "b.py", "line": 4, "msg": "bad type"}]},
                "dead_code": {
                    "items": [
                        {"file": "c.py", "line": 1, "type": "import", "msg": "unused import 'os'", "confidence": 90}
                    ]
                },
                "mutation": {"skipped": "no mutation_paths"},
            }
        },
        errors={"app.semgrep": "ToolError: boom\nmore"},
    )
    found = findings.from_snapshot(snap)
    assert [(f["tool"], f["severity"]) for f in found] == [
        ("pyright", "error"),
        ("semgrep", "error"),
        ("complexity", "warning"),
        ("dead_code", "warning"),
    ]
    cc = found[2]
    assert (cc["file"], cc["symbol"], cc["rule"]) == ("a.py", "f", "cc-over-limit")
    assert found[1]["rule"] == "tool-failed"


def test_key_ignores_line_numbers() -> None:
    a = findings.make("app", "pyright", "error", "error", "bad type", "b.py", 4)
    b = findings.make("app", "pyright", "error", "error", "bad type", "b.py", 40)
    assert a["key"] == b["key"]


def test_mark_new() -> None:
    old = snapshot({"app": {"pyright": {"items": [{"file": "b.py", "line": 4, "msg": "old"}]}}})
    new = snapshot(
        {
            "app": {
                "pyright": {
                    "items": [{"file": "b.py", "line": 9, "msg": "old"}, {"file": "b.py", "line": 12, "msg": "fresh"}]
                },
                "semgrep": {"items": [{"file": "x.py", "line": 1, "code": "r", "severity": "warning", "msg": "m"}]},
            }
        }
    )
    marked = {f["message"]: f["new"] for f in findings.mark_new(findings.from_snapshot(new), old)}
    assert marked == {"old": False, "fresh": True, "m": None}  # semgrep didn't run before: unknown
    assert all(f["new"] is None for f in findings.mark_new(findings.from_snapshot(new), None))


def test_write_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "f.jsonl"
    findings.write_jsonl([findings.make("a", "t", "r", "info", "é")], out)
    assert out.read_text().count("\n") == 1
    assert "é" in out.read_text()


LINT_IMPORTS = """
=============
Import Linter
=============

The shadow lane never sends KEPT
app.core imports nothing from services, agents or api BROKEN

Contracts: 1 kept, 1 broken.

----------------
Broken contracts
----------------

app.core imports nothing from services, agents or api
------------------------------------------------------

app.core is not allowed to import app.services:

- app.core.integrations -> app.services.nightscout (l.12)

- app.core.health ->
app.services.db (l.3)

-   app.core.bootstrap -> app.services.claude_sdk (l.29, l.30,
l.31)
"""


def test_imports_parse() -> None:
    contracts, broken = imports.parse(LINT_IMPORTS)
    assert [c["status"] for c in contracts] == ["KEPT", "BROKEN"]
    assert broken == {
        "app.core is not allowed to import app.services": [
            "app.core.integrations -> app.services.nightscout",
            "app.core.health -> app.services.db",
            "app.core.bootstrap -> app.services.claude_sdk",
        ]
    }


LINT_IMPORTS_NEW_TYPES = """
Only app.services.claude_sdk imports claude_agent_sdk BROKEN
No import cycles between sibling packages BROKEN

Only app.services.claude_sdk imports claude_agent_sdk
-----------------------------------------------------
Illegal imports of protected package claude_agent_sdk:
- app.agents._shared.claude_nodes -> claude_agent_sdk (l.68, 75)
- app.agents.inner_voice.tools -> claude_agent_sdk (l.21, 22)
wiki/concepts/claude-sdk-integration.md: claude_sdk.py is the one way in.

No import cycles between sibling packages
-----------------------------------------
No cycles are allowed in app.
It could be made acyclic by removing 3 dependencies:
- .core -> .agents (7 imports)
- .services -> .agents (27 imports)
No cycles are allowed in app.agents.han_bot.
It could be made acyclic by removing 1 dependency:
- .nodes -> .graph (1 import)
Remove one of the listed dependencies (move the shared code down a level).
"""


def test_imports_parse_protected_and_cycles() -> None:
    contracts, broken = imports.parse(LINT_IMPORTS_NEW_TYPES)
    assert [c["status"] for c in contracts] == ["BROKEN", "BROKEN"]
    assert broken == {
        "Only the allowed importers may import claude_agent_sdk": [
            "app.agents._shared.claude_nodes -> claude_agent_sdk",
            "app.agents.inner_voice.tools -> claude_agent_sdk",
        ],
        "No import cycles in app": ["app.core -> app.agents", "app.services -> app.agents"],
        "No import cycles in app.agents.han_bot": ["app.agents.han_bot.nodes -> app.agents.han_bot.graph"],
    }


def test_cycle_totals_count_elided_dependencies() -> None:
    out = (
        "No cycles are allowed in app.\n"
        "It could be made acyclic by removing 8 dependencies:\n"
        "- .a -> .b (1 import)\n(and 3 more)\n"
        "No cycles are allowed in app.x.\n"
        "It could be made acyclic by removing 1 dependency:\n- .c -> .d (2 imports)\n"
    )
    assert imports.cycle_totals(out) == 9


def test_documented_catch(tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text(
        "try:\n    x()\nexcept Exception:  # observability-only: recorded, see above\n    log()\n"
        "try:\n    y()\nexcept Exception:\n    log()\n"
    )
    assert semgrep.documented_catch(src, 1, 4) is True
    assert semgrep.documented_catch(src, 5, 8) is False


def test_mutation_names_and_config(tmp_path: Path) -> None:
    assert (
        mutation.function_of("claude_sub_proxy.translate.x_flatten_content__mutmut_3")
        == "claude_sub_proxy.translate.flatten_content"
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docker-compose.yaml").write_text("")
    mutation.configure(tmp_path, ["src/pkg/a.py"])
    text = (tmp_path / "pyproject.toml").read_text()
    assert 'paths_to_mutate = ["src/pkg/a.py"]' in text
    assert 'also_copy = ["docker-compose.yaml", "pyproject.toml", "src"]' in text
    assert 'tests_dir = ["tests"]' in text
    (tmp_path / "pyproject.toml").write_text("")
    mutation.configure(tmp_path, ["src/pkg/a.py"], ["tests/test_a.py"])
    assert 'tests_dir = ["tests/test_a.py"]' in (tmp_path / "pyproject.toml").read_text()


def test_first_party_packages(tmp_path: Path) -> None:
    app = tmp_path / "apps" / "x"
    (app / "src" / "pkg").mkdir(parents=True)
    (app / "src" / "pkg" / "__init__.py").write_text("")
    (app / "app").mkdir()
    (app / "app" / "__init__.py").write_text("")
    (app / "tool.py").write_text("")
    target = Target(name="x", kind="python", root="apps/x", source=("src", "app", "tool.py"), tests=False)
    settings = Settings(targets=(target,), jscpd="j", knip="k", complexity_limit=15, cognitive_limit=15)
    ctx = Context(target=target, checkout=tmp_path, raw_dir=tmp_path, settings=settings)
    assert sorted(first_party_packages(ctx)) == ["app", "pkg", "tool"]


def test_first_party_packages_include_namespace_packages(tmp_path: Path) -> None:
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "core.py").write_text("")  # a namespace package at the root
    (tmp_path / "src" / "beta").mkdir(parents=True)
    (tmp_path / "src" / "beta" / "x.py").write_text("")  # and one inside src/
    target = Target(name="a", kind="python", root=".", source=("acme", "src"), tests=False)
    ctx = Context(target=target, checkout=tmp_path, raw_dir=tmp_path, settings=Settings(targets=(target,)))
    assert sorted(first_party_packages(ctx)) == ["acme", "beta"]
