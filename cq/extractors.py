"""Each tool's result in a snapshot, turned into findings: one extractor per tool.

Most tools share a shape (``Lint``, ``OverLimit``, ``Coded``); the rest have a function of their own.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from cq.finding import Finding, Source, file_of, numbered, split_function

Extractor = Callable[[Source, dict], Iterator[Finding]]


# ── The shapes several tools share ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Lint:
    """A linter or type checker: each item a code, a message, a file and a line, all at one severity."""

    severity: str

    def __call__(self, src: Source, d: dict) -> Iterator[Finding]:
        """One finding per item; an item without a code is ruled by the tool's name."""
        for i in d.get("items", []):
            yield src.finding(str(i.get("code") or src.tool), self.severity, i["msg"], i["file"], i.get("line"))


@dataclass(frozen=True)
class OverLimit:
    """A per-function score with a limit: each of the worst functions over it is a warning."""

    rule: str
    field: str
    label: str

    def __call__(self, src: Source, d: dict) -> Iterator[Finding]:
        """One finding per function above the result's limit (15 when it names none)."""
        for w in d.get("worst", []):
            if w[self.field] > d.get("limit", 15):
                file, symbol = split_function(w["function"])
                message = f"{self.label} {w[self.field]} > {d.get('limit')}"
                yield src.finding(self.rule, "warning", message, file, symbol=symbol, value=w[self.field])


def code_of(i: dict) -> str:
    """What a hit says: its rule code."""
    return i["code"]


def code_and_message(i: dict) -> str:
    """What a hit says: its rule code and message (one rule can say several things)."""
    return f"{i['code']}@{i['msg']}"


def vale_match(i: dict) -> str:
    """What a vale hit says: its rule and the matched words."""
    return f"{i['code']}@{i['match'].lower()}"


@dataclass(frozen=True)
class Coded:
    """Items with a code, a message, a file, a line and the source line's fingerprint (semgrep, qlty, aislop, vale)."""

    what: Callable[[dict], str]
    severity: str = ""  # one severity for every item; empty: each item's own

    def __call__(self, src: Source, d: dict) -> Iterator[Finding]:
        """One finding per item, keyed by what it says and where (see ``numbered``)."""
        for i, symbol in numbered(d.get("items", []), self.what):
            yield src.finding(i["code"], self.severity or i["severity"], i["msg"], i["file"], i["line"], symbol=symbol)


# ── One function per tool with a shape of its own ────────────────────────────────────────


def ruff(src: Source, d: dict) -> Iterator[Finding]:
    """ruff's lint findings, and one for the files ruff format would change."""
    yield from Lint("warning")(src, d)
    if d.get("unformatted_files"):
        yield src.finding("format", "warning", f"{d['unformatted_files']} file(s) need ruff format")


def tests(src: Source, d: dict) -> Iterator[Finding]:
    """Failing tests, coverage under the repo's own floor, and files under half covered."""
    results = d.get("results", {})
    if results.get("failed") or results.get("errors"):
        message = f"{results.get('failed', 0)} failed, {results.get('errors', 0)} errors"
        yield src.finding("failing-tests", "error", message)
    cov = d.get("coverage", {})
    floor = cov.get("fail_under")
    if floor is not None and cov.get("percent", 100) < floor:
        yield src.finding("coverage-below-floor", "error", f"coverage {cov['percent']}% < fail_under {floor}%")
    for f in cov.get("worst_files", []):
        if f["percent"] < 50:
            message = f"{f['percent']}% covered, {f['missing']} lines missing"
            yield src.finding("low-file-coverage", "info", message, f["file"])


def maintainability(src: Source, d: dict) -> Iterator[Finding]:
    """Files whose maintainability index ranks below A."""
    for w in d.get("worst", []):
        if w["rank"] != "A":
            message = f"maintainability index {w['mi']} (rank {w['rank']}, {w['lines']} lines)"
            yield src.finding(f"mi-rank-{w['rank']}", "warning", message, w["file"], value=w["mi"])


def dead_code(src: Source, d: dict) -> Iterator[Finding]:
    """vulture's candidates: a warning from 90% confidence, below that a lead to check."""
    for i in d.get("items", []):
        severity = "warning" if i["confidence"] >= 90 else "info"
        message = f"{i['msg']} ({i['confidence']}% confidence)"
        yield src.finding(f"unused-{i['type']}", severity, message, i["file"], i["line"], symbol=i["msg"])


def duplication(src: Source, d: dict) -> Iterator[Finding]:
    """jscpd's largest clones, at the first copy's file."""
    for c in d.get("top", []):
        a, b = file_of(c["a"]), file_of(c["b"])
        message = f"{c['lines']} duplicated lines: {c['a']} and {c['b']}"
        yield src.finding("clone", "info", message, a, symbol=f"{a}~{b}", value=c["lines"])


def docstrings(src: Source, d: dict) -> Iterator[Finding]:
    """The files missing the most docstrings, and each symbol without one."""
    for f in d.get("worst_files", []):
        message = f"{f['missing']} functions/classes without a docstring"
        yield src.finding("missing-docstrings", "info", message, f["file"], value=f["missing"])
    for i in d.get("items", []):
        what = "module" if i["symbol"] == "(module)" else i["symbol"]
        yield src.finding(
            "missing-docstring",
            "info",
            f"{what} has no docstring",
            i["file"],
            i["line"],
            symbol=i["symbol"],
            start_line=i.get("start_line", i["line"]),
            end_line=i.get("end_line"),
        )


def imports(src: Source, d: dict) -> Iterator[Finding]:
    """Each broken import contract, at the module that starts the forbidden chain."""
    for i in d.get("items", []):
        module = i["chain"].split(" ")[0]
        file = module.replace(".", "/") + ".py"
        yield src.finding("contract-broken", "error", f"{i['rule']}: {i['chain']}", file, symbol=i["chain"])


def deptry_finding(src: Source, i: dict) -> Finding:
    """A deptry issue: missing, transitive or misplaced dependencies are warnings; unused ones are info."""
    severity = "warning" if i["code"] in ("DEP001", "DEP003", "DEP004") else "info"
    message = f"{i['msg']} ({i['files']} file(s))"
    return src.finding(i["code"], severity, message, i["file"], symbol=i["module"])


def knip_finding(src: Source, i: dict) -> Finding:
    """An unused export, dependency or file knip found."""
    message = f"unused {i['kind']}: {i['name']}"
    return src.finding(f"knip-{i['kind']}", "info", message, i["file"], i.get("line"), symbol=i["name"])


def dependencies(src: Source, d: dict) -> Iterator[Finding]:
    """deptry's issues (Python) and knip's (TypeScript): a deptry item carries its code."""
    for i in d.get("items", []):
        yield deptry_finding(src, i) if "code" in i else knip_finding(src, i)


def npm_advisory(src: Source, i: dict) -> Finding:
    """An npm audit advisory: critical and high are errors."""
    severity = "error" if i["severity"] in ("critical", "high") else "warning"
    message = f"{i['package']} {i['range']}: {i['title']} (fix: {i['fix']})"
    return src.finding(f"npm-{i['severity']}", severity, message, "package-lock.json", symbol=i["package"])


def pypi_advisory(src: Source, i: dict) -> Finding:
    """A pip-audit advisory: every one is an error (pip-audit doesn't grade them)."""
    message = f"{i['package']} {i['version']}: {i['ids']} (fix: {i['fix']})"
    return src.finding("pypi-advisory", "error", message, "uv.lock", symbol=f"{i['package']}=={i['version']}")


def vulnerabilities(src: Source, d: dict) -> Iterator[Finding]:
    """npm audit's and pip-audit's advisories: an npm item carries its severity."""
    for i in d.get("items", []):
        yield npm_advisory(src, i) if "severity" in i else pypi_advisory(src, i)


def where_now(i: dict) -> str:
    """How a history secret's path stands at HEAD, for its message (empty when unchanged or unknown)."""
    now = i.get("head_file")
    if not now or now == i["file"]:
        return ""
    return "; the file has since been deleted" if now == "deleted" else f"; the file is now {now}"


def in_history(i: dict) -> dict[str, str]:
    """A history secret's symbol (its commit: a rotated key comes in a new commit, so it is a new finding)
    and where its file is at HEAD."""
    return {"symbol": f"{i['code']}@{i['commit']}:{i['line']}", "head_file": i.get("head_file", i["file"])}


def secrets(src: Source, d: dict) -> Iterator[Finding]:
    """Secrets in the tree (errors), in history (warnings) and live keys in history (errors)."""
    for i in d.get("tree_items", []):
        message = f"{i['code']} committed in the current tree: remove and rotate"
        yield src.finding(
            f"in-tree-{i['code']}", "error", message, i["file"], i["line"], symbol=f"{i['code']}@{i['line']}"
        )
    for i in d.get("items", []):
        message = f"{i['code']} in git history (commit {i['commit']}, {i['date']}{where_now(i)}): rotate if still valid"
        yield src.finding(f"in-history-{i['code']}", "warning", message, i["file"], i["line"], **in_history(i))
    for i in d.get("live_items", []):
        message = (
            f"{i['code']} key in git history (commit {i['commit']}, {i['date']}{where_now(i)}) is LIVE:"
            " its provider accepted it"
        )
        yield src.finding(f"live-{i['code']}", "error", message, i["file"], i["line"], **in_history(i))


def mutation(src: Source, d: dict) -> Iterator[Finding]:
    """Each surviving mutant, with its diff."""
    for i in d.get("items", []):
        message = f"a mutant of {i['function']} survived: no test notices this change"
        yield src.finding("surviving-mutant", "info", message, symbol=i["mutant"], diff=i.get("diff", ""))


def hotspots(src: Source, d: dict) -> Iterator[Finding]:
    """The ten worst churn × complexity files; often changed and over the CC limit is a warning."""
    for i in d.get("items", [])[:10]:
        severity = "warning" if i["commits"] >= 5 and i["worst_cc"] > 15 else "info"
        message = (
            f"{i['commits']} commits in {d.get('window_days')} days × complexity {i['complexity']}"
            f" (worst function CC {i['worst_cc']}): score {i['score']}"
        )
        yield src.finding("hotspot", severity, message, i["file"], value=i["score"])


def suppressions(src: Source, d: dict) -> Iterator[Finding]:
    """Each suppression comment: a warning when it gives no reason."""
    for i in d.get("items", []):
        severity, prefix = ("info", "") if i["reason"] else ("warning", "no reason given: ")
        symbol = f"{i['kind']}:{i['text'][:80]}"
        yield src.finding(i["kind"], severity, prefix + i["text"], i["file"], i["line"], symbol=symbol)


def containers(src: Source, d: dict) -> Iterator[Finding]:
    """hadolint's Dockerfile findings, and the image's high and critical vulnerabilities from Trivy."""
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i in d.get("dockerfile", {}).get("items", []):
        severity = "warning" if i["level"] in ("error", "warning") else "info"
        yield src.finding(f"hadolint-{i['code']}", severity, i["msg"], i["file"], i["line"], symbol=i["code"])
    image = d.get("image", {})
    for i in image.get("items", []):
        fixable = i["fix"] != "none yet"
        severity = "error" if fixable or i["severity"] == "critical" else "warning"
        message = f"{i['package']} {i['version']}: {i['id']} {i['title']} (fix: {i['fix']})"
        symbol = f"{i['package']}:{i['id']}"
        yield src.finding(f"image-{i['severity']}", severity, message, image.get("image", ""), symbol=symbol)


def freshness(src: Source, d: dict) -> Iterator[Finding]:
    """Outdated direct dependencies: a major version behind is a warning."""
    for i in d.get("items", []):
        rule, severity = ("major-behind", "warning") if i["major"] else ("outdated", "info")
        message = f"{i['package']} {i['current']} → {i['latest']} ({i['years']} years behind)"
        yield src.finding(rule, severity, message, symbol=i["package"], value=i["years"])


def code_at_line(i: dict) -> str:
    """A shell or workflow finding's symbol: its code and line (these tools give no source fingerprint)."""
    return f"{i['code']}@{i['line']}"


def scripts(src: Source, d: dict) -> Iterator[Finding]:
    """shellcheck, actionlint and zizmor findings."""
    levels = {"error": "error", "warning": "warning"}
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i in d.get("shell", {}).get("items", []):
        severity = levels.get(i["level"], "info")
        yield src.finding(i["code"], severity, i["msg"], i["file"], i["line"], symbol=code_at_line(i))
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i in d.get("workflows", {}).get("items", []):
        rule = f"actionlint-{i['code']}"
        yield src.finding(rule, "warning", i["msg"], i["file"], i["line"], symbol=code_at_line(i))
    # aislop-ignore-next-line ai-slop/python-chained-dict-get -- stored snapshots, older ones included, may lack this part
    for i, symbol in numbered(d.get("workflow_security", {}).get("items", []), code_of):
        yield src.finding(f"zizmor-{i['code']}", "warning", i["msg"], i["file"], i["line"], symbol=symbol)


def links(src: Source, d: dict) -> Iterator[Finding]:
    """Each broken link, at the file that has it."""
    for i in d.get("items", []):
        message = f"broken link to {i['target']} {i['status']}".strip()
        yield src.finding("broken-link", "warning", message, i["file"], symbol=i["target"])


def pyscn(src: Source, d: dict) -> Iterator[Finding]:
    """pyscn's clone pairs, at the first copy's file, keyed by the other copy's file."""
    pairs = [i | {"file": file_of(i["a"])} for i in d.get("items", [])]
    for i, symbol in numbered(pairs, lambda i: file_of(i["b"])):
        message = f"{i['lines']} lines {round(100 * i['similarity'])}% similar: {i['a']} and {i['b']}"
        yield src.finding(f"clone-type-{i['type']}", "info", message, i["file"], symbol=symbol)


def fallow(src: Source, d: dict) -> Iterator[Finding]:
    """fallow's clones, and its unhealthy functions (complex and poorly covered)."""
    for c in d.get("clones", []):
        message = f"{c['lines']} similar lines: {c['a']} and {c['b']}"
        yield src.finding(
            "clone", "info", message, file_of(c["a"]), symbol=c.get("fingerprint") or f"{c['a']}~{c['b']}"
        )
    for i, symbol in numbered(d.get("items", []), lambda i: i["function"]):
        message = f"{i['function']}: cyclomatic {i['cyclomatic']}, cognitive {i['cognitive']}, CRAP {i['crap']}"
        yield src.finding("function-health", i["severity"], message, i["file"], i["line"], symbol=symbol)


def cspell(src: Source, d: dict) -> Iterator[Finding]:
    """Each unknown word's place; the word itself is never stored (it can be a name or a key fragment)."""
    message = "unknown word (a typo, or a name or key fragment: the word is not stored)"
    for i, symbol in numbered(d.get("items", []), lambda i: "unknown-word"):
        yield src.finding("unknown-word", "info", message, i["file"], i["line"], symbol=symbol)


EXTRACTORS: dict[str, Extractor] = {
    "ruff": ruff,
    "pyright": Lint("error"),
    "eslint": Lint("warning"),
    "typecheck": Lint("error"),
    "tests": tests,
    "complexity": OverLimit("cc-over-limit", "cc", "cyclomatic complexity"),
    "cognitive": OverLimit("cognitive-over-limit", "score", "cognitive complexity"),
    "maintainability": maintainability,
    "dead_code": dead_code,
    "duplication": duplication,
    "docstrings": docstrings,
    "semgrep": Coded(code_of),
    "imports": imports,
    "dependencies": dependencies,
    "vulnerabilities": vulnerabilities,
    "secrets": secrets,
    "mutation": mutation,
    "hotspots": hotspots,
    "suppressions": suppressions,
    "containers": containers,
    "freshness": freshness,
    "scripts": scripts,
    "links": links,
    "radarlint": Coded(code_and_message),
    "smells": Coded(code_and_message, "warning"),
    "aislop": Coded(code_and_message),
    "pyscn": pyscn,
    "fallow": fallow,
    "vale": Coded(vale_match),
    "cspell": cspell,
}
