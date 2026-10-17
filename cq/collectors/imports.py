"""Architecture contracts (import-linter): code_quality/contracts/<target>.ini, when one exists.

Each contract is a rule about who may import whom, e.g. the danger-path shadow lane never
reaching a sender. A broken contract lists the imports that break it, read from lint-imports'
report for each contract type used: forbidden ("X is not allowed to import Y:" and its chains),
protected ("Illegal imports of protected package X:") and acyclic_siblings ("No cycles are
allowed in X." and the dependencies whose removal would break the cycle, relative to X).
"""

import re

from cq.config import PROJECT_DIR
from cq.context import Context
from cq.proc import run

KINDS = ("python",)

CONTRACTS_DIR = PROJECT_DIR / "contracts"
STATUS = re.compile(r"^(?P<name>.+?) (?P<status>KEPT|BROKEN)$")
VIOLATION = re.compile(r"^-\s+(?P<chain>app\.[\w.]+(?: ->\s*\S+)?)")
PROTECTED = re.compile(r"^Illegal imports of protected package (?P<package>[\w.]+):$")
CYCLE = re.compile(r"^No cycles are allowed in (?P<ancestor>[\w.]+)\.$")
# "It could be made acyclic by removing N dependencies:" lists at most five, then "(and M more)".
CYCLE_TOTAL = re.compile(r"^It could be made acyclic by removing (?P<n>\d+) dependenc")
CYCLE_EDGE = re.compile(r"^-\s+\.(?P<src>[\w.]+) -> \.(?P<dst>[\w.]+)")


def cycle_totals(out: str) -> int:
    """How many dependencies the cycle reports would remove, including those lint-imports elides."""
    return sum(int(m["n"]) for m in map(CYCLE_TOTAL.match, out.splitlines()) if m)


def parse(out: str) -> tuple[list[dict], dict[str, list[str]]]:
    """(contracts with their status, the import chains per broken contract) from lint-imports' text."""
    # lint-imports wraps long lines; join wrapped continuations onto the line they belong to.
    text = re.sub(r"->\s*\n\s*", "-> ", out)
    text = re.sub(r" \n(?=[\w.(])", " ", text)
    contracts = [m.groupdict() for m in map(STATUS.match, text.splitlines()) if m]
    broken: dict[str, list[str]] = {}
    current = None
    ancestor = ""
    for line in text.splitlines():
        if line.endswith(":") and " is not allowed to import " in line:
            current, ancestor = line.rstrip(":"), ""
            broken.setdefault(current, [])
        elif m := PROTECTED.match(line):
            current, ancestor = f"Only the allowed importers may import {m['package']}", ""
            broken.setdefault(current, [])
        elif m := CYCLE.match(line):
            current, ancestor = f"No import cycles in {m['ancestor']}", m["ancestor"]
            broken.setdefault(current, [])
        elif current and ancestor and (m := CYCLE_EDGE.match(line.strip())):
            broken[current].append(f"{ancestor}.{m['src']} -> {ancestor}.{m['dst']}")
        elif current and not ancestor and (m := VIOLATION.match(line.strip())):
            broken[current].append(" ".join(m["chain"].split()))
    return contracts, broken


def collect(ctx: Context) -> dict:
    """lint-imports exits 1 when a contract is broken; that is the measurement."""
    config = CONTRACTS_DIR / f"{ctx.target.name}.ini"
    if not config.exists():
        return {"skipped": f"no contracts/{ctx.target.name}.ini"}
    out = run(
        ["lint-imports", "--config", str(config), "--no-cache"],
        ctx.app_dir,
        ok_codes=(0, 1),
        env={"PYTHONPATH": str(ctx.app_dir)},
    )
    (ctx.raw_dir / "lint-imports.txt").write_text(out)
    contracts, broken = parse(out)
    items = [{"rule": rule, "chain": chain} for rule, chains in broken.items() for chain in chains]
    # Cycle reports list five dependencies at most; count the elided ones too, so the gate's
    # ratchet still rises when a cycle is added to a long report.
    listed_cycle_edges = sum(len(c) for rule, c in broken.items() if rule.startswith("No import cycles in "))
    elided = max(0, cycle_totals(out) - listed_cycle_edges)
    return {
        "contracts": len(contracts),
        "kept": sum(1 for c in contracts if c["status"] == "KEPT"),
        "broken": sum(1 for c in contracts if c["status"] == "BROKEN"),
        "violations": len(items) + elided,
        "contract_status": contracts,
        "items": items[:80],
    }
