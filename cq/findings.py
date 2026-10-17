"""Every tool's findings in one shape: the work list an agent (han-dev-watch) can act on.

Each finding has the same fields whatever tool produced it:

    target, tool, rule, severity (error | warning | info), file, line, symbol, message, key, new

``key`` identifies the finding across runs without its line number (lines shift when code above
moves), so "new since the previous snapshot" means a problem that did not exist before, not one
that moved. A run writes them to runs/<run>/findings.jsonl, most severe first.
"""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

SEVERITIES = ("error", "warning", "info")

Finding = dict


def make(target: str, tool: str, rule: str, severity: str, message: str, file: str = "", line: int | None = None,
         symbol: str = "", **extra: object) -> Finding:  # fmt: skip
    """One finding, with its stable key."""
    key = f"{target}|{tool}|{rule}|{file}|{symbol or message}"
    return {
        "target": target,
        "tool": tool,
        "rule": rule,
        "severity": severity,
        "file": file,
        "line": line,
        "symbol": symbol,
        "message": message,
        "key": key,
        **extra,
    }


def split_function(qualified: str) -> tuple[str, str]:
    """``path/file.py::Cls.method`` → (file, symbol)."""
    file, _, symbol = qualified.partition("::")
    return file, symbol


def _lint(t: str, tool: str, data: dict, severity: str) -> Iterator[Finding]:
    for i in data.get("items", []):
        yield make(t, tool, str(i.get("code") or tool), severity, i["msg"], i["file"], i.get("line"))


def _ruff(t: str, d: dict) -> Iterator[Finding]:
    yield from _lint(t, "ruff", d, "warning")
    if d.get("unformatted_files"):
        yield make(t, "ruff", "format", "warning", f"{d['unformatted_files']} file(s) need ruff format")


def _pyright(t: str, d: dict) -> Iterator[Finding]:
    yield from _lint(t, "pyright", d, "error")


def _eslint(t: str, d: dict) -> Iterator[Finding]:
    yield from _lint(t, "eslint", d, "warning")


def _typecheck(t: str, d: dict) -> Iterator[Finding]:
    yield from _lint(t, "typecheck", d, "error")


def _tests(t: str, d: dict) -> Iterator[Finding]:
    results = d.get("results", {})
    if results.get("failed") or results.get("errors"):
        yield make(
            t,
            "tests",
            "failing-tests",
            "error",
            f"{results.get('failed', 0)} failed, {results.get('errors', 0)} errors",
        )
    cov = d.get("coverage", {})
    floor = cov.get("fail_under")
    if floor is not None and cov.get("percent", 100) < floor:
        yield make(t, "tests", "coverage-below-floor", "error", f"coverage {cov['percent']}% < fail_under {floor}%")
    for f in cov.get("worst_files", []):
        if f["percent"] < 50:
            yield make(
                t,
                "tests",
                "low-file-coverage",
                "info",
                f"{f['percent']}% covered, {f['missing']} lines missing",
                f["file"],
            )


def _complexity(t: str, d: dict) -> Iterator[Finding]:
    for w in d.get("worst", []):
        if w["cc"] > d.get("limit", 15):
            file, symbol = split_function(w["function"])
            yield make(
                t,
                "complexity",
                "cc-over-limit",
                "warning",
                f"cyclomatic complexity {w['cc']} > {d.get('limit')}",
                file,
                symbol=symbol,
                value=w["cc"],
            )


def _cognitive(t: str, d: dict) -> Iterator[Finding]:
    for w in d.get("worst", []):
        if w["score"] > d.get("limit", 15):
            file, symbol = split_function(w["function"])
            yield make(
                t,
                "cognitive",
                "cognitive-over-limit",
                "warning",
                f"cognitive complexity {w['score']} > {d.get('limit')}",
                file,
                symbol=symbol,
                value=w["score"],
            )


def _maintainability(t: str, d: dict) -> Iterator[Finding]:
    for w in d.get("worst", []):
        if w["rank"] != "A":
            yield make(
                t,
                "maintainability",
                f"mi-rank-{w['rank']}",
                "warning",
                f"maintainability index {w['mi']} (rank {w['rank']}, {w['lines']} lines)",
                w["file"],
                value=w["mi"],
            )


def _dead_code(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        severity = "warning" if i["confidence"] >= 90 else "info"
        yield make(
            t,
            "dead_code",
            f"unused-{i['type']}",
            severity,
            f"{i['msg']} ({i['confidence']}% confidence)",
            i["file"],
            i["line"],
            symbol=i["msg"],
        )


def _duplication(t: str, d: dict) -> Iterator[Finding]:
    for c in d.get("top", []):
        a, b = c["a"].rsplit(":", 1)[0], c["b"].rsplit(":", 1)[0]
        yield make(
            t,
            "duplication",
            "clone",
            "info",
            f"{c['lines']} duplicated lines: {c['a']} and {c['b']}",
            c["a"].rsplit(":", 1)[0],
            symbol=f"{a}~{b}",
            value=c["lines"],
        )


def _docstrings(t: str, d: dict) -> Iterator[Finding]:
    for f in d.get("worst_files", []):
        yield make(
            t,
            "docstrings",
            "missing-docstrings",
            "info",
            f"{f['missing']} functions/classes without a docstring",
            f["file"],
            value=f["missing"],
        )
    for i in d.get("items", []):
        what = "module" if i["symbol"] == "(module)" else i["symbol"]
        yield make(
            t,
            "docstrings",
            "missing-docstring",
            "info",
            f"{what} has no docstring",
            i["file"],
            i["line"],
            symbol=i["symbol"],
            start_line=i.get("start_line", i["line"]),
            end_line=i.get("end_line"),
        )


def _semgrep(t: str, d: dict) -> Iterator[Finding]:
    for i, symbol in numbered(d.get("items", []), lambda i: i["code"]):
        yield make(t, "semgrep", i["code"], i["severity"], i["msg"], i["file"], i["line"], symbol=symbol)


def _imports(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        module = i["chain"].split(" ")[0]
        yield make(
            t,
            "imports",
            "contract-broken",
            "error",
            f"{i['rule']}: {i['chain']}",
            module.replace(".", "/") + ".py",
            symbol=i["chain"],
        )


def _dependencies(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        if "code" in i:  # deptry
            severity = "warning" if i["code"] in ("DEP001", "DEP003", "DEP004") else "info"
            yield make(
                t,
                "dependencies",
                i["code"],
                severity,
                f"{i['msg']} ({i['files']} file(s))",
                i["file"],
                symbol=i["module"],
            )
        else:  # knip
            yield make(
                t,
                "dependencies",
                f"knip-{i['kind']}",
                "info",
                f"unused {i['kind']}: {i['name']}",
                i["file"],
                i.get("line"),
                symbol=i["name"],
            )


def _vulnerabilities(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        if "severity" in i:  # npm
            severity = "error" if i["severity"] in ("critical", "high") else "warning"
            yield make(
                t,
                "vulnerabilities",
                f"npm-{i['severity']}",
                severity,
                f"{i['package']} {i['range']}: {i['title']} (fix: {i['fix']})",
                "package-lock.json",
                symbol=i["package"],
            )
        else:
            yield make(
                t,
                "vulnerabilities",
                "pypi-advisory",
                "error",
                f"{i['package']} {i['version']}: {i['ids']} (fix: {i['fix']})",
                "uv.lock",
                symbol=f"{i['package']}=={i['version']}",
            )


def where_now(i: dict) -> str:
    """How a history secret's path stands at HEAD, for its message (empty when unchanged or unknown)."""
    now = i.get("head_file")
    if not now or now == i["file"]:
        return ""
    return "; the file has since been deleted" if now == "deleted" else f"; the file is now {now}"


def _secrets(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("tree_items", []):
        yield make(
            t,
            "secrets",
            f"in-tree-{i['code']}",
            "error",
            f"{i['code']} committed in the current tree: remove and rotate",
            i["file"],
            i["line"],
            symbol=f"{i['code']}@{i['line']}",
        )
    for i in d.get("items", []):
        yield make(
            t,
            "secrets",
            f"in-history-{i['code']}",
            "warning",
            f"{i['code']} in git history (commit {i['commit']}, {i['date']}{where_now(i)}): rotate if still valid",
            i["file"],
            i["line"],
            symbol=f"{i['code']}@{i['commit']}:{i['line']}",
            head_file=i.get("head_file", i["file"]),
        )
    for i in d.get("live_items", []):
        yield make(
            t,
            "secrets",
            f"live-{i['code']}",
            "error",
            f"{i['code']} key in git history (commit {i['commit']}, {i['date']}{where_now(i)}) is LIVE:"
            " its provider accepted it",
            i["file"],
            i["line"],
            symbol=f"{i['code']}@{i['commit']}:{i['line']}",  # a rotated key comes in a new commit: a new finding
            head_file=i.get("head_file", i["file"]),
        )


def _mutation(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        yield make(
            t,
            "mutation",
            "surviving-mutant",
            "info",
            f"a mutant of {i['function']} survived: no test notices this change",
            symbol=i["mutant"],
            diff=i.get("diff", ""),
        )


def _hotspots(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", [])[:10]:
        severity = "warning" if i["commits"] >= 5 and i["worst_cc"] > 15 else "info"
        yield make(
            t,
            "hotspots",
            "hotspot",
            severity,
            f"{i['commits']} commits in {d.get('window_days')} days × complexity {i['complexity']} (worst function CC {i['worst_cc']}): score {i['score']}",
            i["file"],
            value=i["score"],
        )


def _suppressions(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        yield make(
            t,
            "suppressions",
            i["kind"],
            "info" if i["reason"] else "warning",
            ("" if i["reason"] else "no reason given: ") + i["text"],
            i["file"],
            i["line"],
            symbol=f"{i['kind']}:{i['text'][:80]}",
        )


def _containers(t: str, d: dict) -> Iterator[Finding]:
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i in d.get("dockerfile", {}).get("items", []):
        yield make(
            t,
            "containers",
            f"hadolint-{i['code']}",
            "warning" if i["level"] in ("error", "warning") else "info",
            i["msg"],
            i["file"],
            i["line"],
            symbol=i["code"],
        )
    image = d.get("image", {})
    for i in image.get("items", []):
        fixable = i["fix"] != "none yet"
        severity = "error" if fixable or i["severity"] == "critical" else "warning"
        yield make(
            t,
            "containers",
            f"image-{i['severity']}",
            severity,
            f"{i['package']} {i['version']}: {i['id']} {i['title']} (fix: {i['fix']})",
            image.get("image", ""),
            symbol=f"{i['package']}:{i['id']}",
        )


def _freshness(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        yield make(
            t,
            "freshness",
            "major-behind" if i["major"] else "outdated",
            "warning" if i["major"] else "info",
            f"{i['package']} {i['current']} → {i['latest']} ({i['years']} years behind)",
            symbol=i["package"],
            value=i["years"],
        )


def _scripts(t: str, d: dict) -> Iterator[Finding]:
    levels = {"error": "error", "warning": "warning"}
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i in d.get("shell", {}).get("items", []):
        yield make(
            t,
            "scripts",
            i["code"],
            levels.get(i["level"], "info"),
            i["msg"],
            i["file"],
            i["line"],
            symbol=f"{i['code']}@{i['line']}",
        )
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i in d.get("workflows", {}).get("items", []):
        yield make(
            t,
            "scripts",
            f"actionlint-{i['code']}",
            "warning",
            i["msg"],
            i["file"],
            i["line"],
            symbol=f"{i['code']}@{i['line']}",
        )
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i, symbol in numbered(d.get("workflow_security", {}).get("items", []), lambda i: i["code"]):
        yield make(t, "scripts", f"zizmor-{i['code']}", "warning", i["msg"], i["file"], i["line"], symbol=symbol)


def _links(t: str, d: dict) -> Iterator[Finding]:
    for i in d.get("items", []):
        yield make(
            t,
            "links",
            "broken-link",
            "warning",
            f"broken link to {i['target']} {i['status']}".strip(),
            i["file"],
            symbol=i["target"],
        )


def numbered(items: list[dict], what: Callable[[dict], str]) -> Iterator[tuple[dict, str]]:
    """Each item with a symbol of what it says and where: its source line's fingerprint (``at``).

    The fingerprint keeps a hit's key when lines shift above it, and tells two hits of one rule in
    a file apart. Only hits on identical lines are numbered (``…#2``): nothing else separates them.
    """
    seen: dict[tuple[str, str], int] = {}
    for i in sorted(items, key=lambda i: (i["file"], i.get("line") or 0)):
        base = f"{what(i)}@{i.get('at', '')}"
        n = seen[(i["file"], base)] = seen.get((i["file"], base), 0) + 1
        yield i, base if n == 1 else f"{base}#{n}"


def _radarlint(t: str, d: dict) -> Iterator[Finding]:
    for i, symbol in numbered(d.get("items", []), lambda i: f"{i['code']}@{i['msg']}"):
        yield make(t, "radarlint", i["code"], i["severity"], i["msg"], i["file"], i["line"], symbol=symbol)


def _smells(t: str, d: dict) -> Iterator[Finding]:
    for i, symbol in numbered(d.get("items", []), lambda i: f"{i['code']}@{i['msg']}"):
        yield make(t, "smells", i["code"], "warning", i["msg"], i["file"], i["line"], symbol=symbol)


def _aislop(t: str, d: dict) -> Iterator[Finding]:
    for i, symbol in numbered(d.get("items", []), lambda i: f"{i['code']}@{i['msg']}"):
        yield make(t, "aislop", i["code"], i["severity"], i["msg"], i["file"], i["line"], symbol=symbol)


def _pyscn(t: str, d: dict) -> Iterator[Finding]:
    for i, symbol in numbered(
        [i | {"file": i["a"].rsplit(":", 1)[0]} for i in d.get("items", [])], lambda i: i["b"].rsplit(":", 1)[0]
    ):
        yield make(
            t,
            "pyscn",
            f"clone-type-{i['type']}",
            "info",
            f"{i['lines']} lines {round(100 * i['similarity'])}% similar: {i['a']} and {i['b']}",
            i["a"].rsplit(":", 1)[0],
            symbol=symbol,
        )


def _fallow(t: str, d: dict) -> Iterator[Finding]:
    for c in d.get("clones", []):
        yield make(
            t,
            "fallow",
            "clone",
            "info",
            f"{c['lines']} similar lines: {c['a']} and {c['b']}",
            c["a"].rsplit(":", 1)[0],
            symbol=c.get("fingerprint") or f"{c['a']}~{c['b']}",
        )
    for i, symbol in numbered(d.get("items", []), lambda i: i["function"]):
        yield make(
            t,
            "fallow",
            "function-health",
            i["severity"],
            f"{i['function']}: cyclomatic {i['cyclomatic']}, cognitive {i['cognitive']}, CRAP {i['crap']}",
            i["file"],
            i["line"],
            symbol=symbol,
        )


def _vale(t: str, d: dict) -> Iterator[Finding]:
    for i, symbol in numbered(d.get("items", []), lambda i: f"{i['code']}@{i['match'].lower()}"):
        yield make(t, "vale", i["code"], i["severity"], i["msg"], i["file"], i["line"], symbol=symbol)


def _cspell(t: str, d: dict) -> Iterator[Finding]:
    message = "unknown word (a typo, or a name or key fragment: the word is not stored)"
    for i, symbol in numbered(d.get("items", []), lambda i: "unknown-word"):
        yield make(t, "cspell", "unknown-word", "info", message, i["file"], i["line"], symbol=symbol)


EXTRACTORS: dict[str, Callable[[str, dict], Iterator[Finding]]] = {
    "ruff": _ruff,
    "pyright": _pyright,
    "eslint": _eslint,
    "typecheck": _typecheck,
    "tests": _tests,
    "complexity": _complexity,
    "cognitive": _cognitive,
    "maintainability": _maintainability,
    "dead_code": _dead_code,
    "duplication": _duplication,
    "docstrings": _docstrings,
    "semgrep": _semgrep,
    "imports": _imports,
    "dependencies": _dependencies,
    "vulnerabilities": _vulnerabilities,
    "secrets": _secrets,
    "mutation": _mutation,
    "hotspots": _hotspots,
    "suppressions": _suppressions,
    "containers": _containers,
    "freshness": _freshness,
    "scripts": _scripts,
    "links": _links,
    "radarlint": _radarlint,
    "smells": _smells,
    "aislop": _aislop,
    "pyscn": _pyscn,
    "fallow": _fallow,
    "vale": _vale,
    "cspell": _cspell,
}


def from_snapshot(snap: dict) -> list[Finding]:
    """Every finding in a snapshot, most severe first."""
    out: list[Finding] = []
    for target, tools in snap["targets"].items():
        for tool, data in tools.items():
            if tool in EXTRACTORS and isinstance(data, dict) and "skipped" not in data:
                out.extend(EXTRACTORS[tool](target, data))
    for name, error in snap.get("errors", {}).items():
        target, _, tool = name.partition(".")
        out.append(
            make(target, tool or "run", "tool-failed", "error", f"the tool did not run: {error.splitlines()[0][:200]}")
        )
    return sorted(out, key=lambda f: (SEVERITIES.index(f["severity"]), f["target"], f["tool"], f["file"]))


def mark_new(findings: list[Finding], previous: dict | None) -> list[Finding]:
    """Flag the findings whose key the previous snapshot did not have.

    A tool the previous snapshot didn't run (new tool, old commit) has nothing to compare with,
    so its findings are not called new.
    """
    if previous is None:
        return [{**f, "new": None} for f in findings]
    before = {f["key"] for f in from_snapshot(previous)}
    ran_before = {(t, tool) for t, tools in previous["targets"].items() for tool in tools}
    return [
        {**f, "new": (f["key"] not in before) if (f["target"], f["tool"]) in ran_before else None} for f in findings
    ]


def write_jsonl(findings: list[Finding], path: Path) -> None:
    """One finding per line."""
    path.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in findings))
