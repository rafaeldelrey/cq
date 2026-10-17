"""Semgrep: the repo's house rules plus the registry's rulesets.

The house rules are a folder of rule files (cq/toolconfig.py): the repo's own, or cq's defaults
(no print, no broad except that logs and carries on, no nested imports, no naive datetimes). The
registry rules (p/python; p/typescript and p/react for the UI) need network to fetch.

A second scan runs the folder's test rules (``tests/*.yml``) over the app's tests, e.g. a test whose
only checks are mock-call assertions.
"""

import json
import re
import shutil
import tempfile
from pathlib import Path

from cq.context import Context
from cq.proc import own, run
from cq.toolconfig import semgrep_rules

KINDS = ("python", "typescript")

REGISTRY = {"python": ["p/python"], "typescript": ["p/typescript", "p/react"]}
SEVERITY = {"ERROR": "error", "WARNING": "warning", "INFO": "info"}
BROAD_EXCEPT = re.compile(r"^\s*except\s*\(?\s*(Base)?Exception\b")
RULE_ID = re.compile(r"^\s*-\s*id:\s*(\S+)", re.MULTILINE)
# Rules matched by the end of their id, whatever prefix a repo gives its house rules.
LOGS_AND_CARRIES_ON = "logs-and-carries-on"
MOCK_ONLY_TESTS = "test-only-asserts-mocks"


def rule_files(folder: Path) -> list[Path]:
    """The rule files directly in a folder (its tests/ subfolder holds the test rules)."""
    return sorted(p for p in folder.glob("*.y*ml") if p.is_file())


def house_rule_ids(folder: Path) -> set[str]:
    """Every rule id the house-rule files define, production and test rules alike.

    By the id's last segment, as ``scan`` records a finding's rule: a namespaced id
    (``company.security.rule``) matches its findings either way.
    """
    files = [*rule_files(folder), *rule_files(folder / "tests")]
    return {m.rsplit(".", 1)[-1] for f in files for m in RULE_ID.findall(f.read_text())}


def documented_catch(path: Path, start: int, end: int) -> bool:
    """Whether a broad ``except`` in the span carries a comment naming its reason (code-standards.md)."""
    lines = path.read_text().splitlines()[start - 1 : end]
    excepts = [line for line in lines if BROAD_EXCEPT.match(line)]
    return bool(excepts) and all("#" in line for line in excepts)


def scan(ctx: Context, configs: list[str], paths: list[str], out_file: Path, excludes: list[str]) -> list[dict]:
    """One semgrep scan; its results as items (documented broad catches dropped)."""
    args = [arg for c in configs for arg in ("--config", c)] + [arg for e in excludes for arg in ("--exclude", e)]
    run(
        [own("semgrep"), "scan", *args, "--metrics", "off", "--json", "-q", "-o", str(out_file), *paths],
        ctx.checkout,
        ok_codes=(0, 1),
    )
    data = json.loads(out_file.read_text())
    items = []
    for r in data["results"]:
        rule = r["check_id"].rsplit(".", 1)[-1]
        path = ctx.checkout / r["path"] if not Path(r["path"]).is_absolute() else Path(r["path"])
        if rule.endswith(LOGS_AND_CARRIES_ON) and documented_catch(path, r["start"]["line"], r["end"]["line"]):
            continue
        items.append(
            {
                "file": ctx.rel(r["path"], ctx.checkout),
                "line": r["start"]["line"],
                "code": rule,
                "severity": SEVERITY.get(r["extra"]["severity"], "info"),
                "msg": " ".join(r["extra"]["message"].split())[:240],
            }
        )
    return [*items, *({"scan_error": e.get("message", "")[:200]} for e in data.get("errors", []))]


def scan_tests(ctx: Context, tests_dir: Path, rules: list[Path]) -> list[dict]:
    """The test rules over the app's tests.

    Semgrep's built-in ignore list skips any folder named ``tests``, even one given as the target,
    so the test files are copied to a neutral folder name and the paths mapped back afterwards.
    """
    with tempfile.TemporaryDirectory(prefix="cq-semgrep-tests-") as tmp:
        suite = Path(tmp) / "suite"
        shutil.copytree(tests_dir, suite, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        found = scan(ctx, [str(r) for r in rules], [str(suite)], ctx.raw_dir / "semgrep-tests.json", [])
        prefix = str(suite.resolve())
        for item in found:
            if "file" in item:
                path = Path(item["file"])
                resolved = path.resolve() if path.is_absolute() else path
                if str(resolved).startswith(prefix):
                    item["file"] = ctx.rel(tests_dir / resolved.relative_to(prefix))
    return found


def scan_all(ctx: Context, folder: Path) -> list[dict]:
    """The production scan, plus the test scan when the app has tests and the folder has test rules."""
    found = scan(
        ctx,
        [*(str(r) for r in rule_files(folder)), *REGISTRY[ctx.target.kind]],
        [str(p) for p in ctx.sources],
        ctx.raw_dir / "semgrep.json",
        ["tests", "*.test.ts", "*.test.tsx"],
    )
    tests_dir = ctx.app_dir / "tests"
    test_rules = rule_files(folder / "tests")
    if ctx.target.kind == "python" and tests_dir.is_dir() and test_rules:
        found += scan_tests(ctx, tests_dir, test_rules)
    return found


def collect(ctx: Context) -> dict:
    """Production rules over the production paths; test rules over the app's tests."""
    folder = semgrep_rules(ctx.settings)
    found = scan_all(ctx, folder)
    items = [i for i in found if "scan_error" not in i]
    house = house_rule_ids(folder)
    by_rule: dict[str, int] = {}
    for item in items:
        by_rule[item["code"]] = by_rule.get(item["code"], 0) + 1
    return {
        "findings": len(items),
        "errors": sum(1 for i in items if i["severity"] == "error"),
        "warnings": sum(1 for i in items if i["severity"] == "warning"),
        "house_rules": sum(1 for i in items if i["code"] in house),
        "mock_only_tests": sum(n for rule, n in by_rule.items() if rule.endswith(MOCK_ONLY_TESTS)),
        "scan_errors": len(found) - len(items),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "items": ctx.with_fingerprints(
            ctx.cap(
                sorted(items, key=lambda i: (("error", "warning", "info").index(i["severity"]), i["file"], i["line"])),
                80,
            )
        ),
    }
