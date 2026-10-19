"""Secrets committed to git: betterleaks for the tree and the history, trufflehog for which are live.

- **betterleaks** (gitleaks' successor, same author, reads the same config: the repo's, or cq's default)
  scans the tracked tree and every commit. Only tracked content is scanned: the tree comes from ``git archive HEAD``, so
  gitignored files like .env are never read. Reports are written with ``--redact``
  to a temporary folder and deleted; only rule, file, line, commit and date are kept.
  The history scan is cached per commit (``betterleaks-history-<config hash>-<head>``; the old
  gitleaks cache is not reused) and the next run scans only the commits since.
- **trufflehog** scans the history and **verifies** each key against its provider, so the report
  says which are still live: the ones to rotate first. Its output carries the secret values, so it
  is never written or logged (``secret_output``); only the detector, file, commit, date and the
  verified flag are kept. Verification needs network: when no key at all can be checked, the
  collector fails (a blind spot) instead of reporting 0 live keys.
"""

import hashlib
import json
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

from cq.config import DEFAULTS_DIR
from cq.context import Context
from cq.proc import ToolError, require, run
from cq.toolconfig import gitleaks_config

KINDS = ("repo",)

DEFAULT_CONFIG = DEFAULTS_DIR / "gitleaks.toml"


def config_hash(config: Path = DEFAULT_CONFIG) -> str:
    """Short hash of the rules: a cached history scan is only reused under the same config and the
    same betterleaks, whose built-in rules (``useDefault = true``) change with the binary."""
    version = run(["betterleaks", "version"], config.parent).strip()
    return hashlib.sha256(config.read_bytes() + version.encode()).hexdigest()[:8]


def betterleaks(args: list[str], cwd: Path, config: Path = DEFAULT_CONFIG) -> list[dict]:
    """One betterleaks scan, as slim findings.

    The report goes to a temporary folder and is deleted: ``--redact`` hides the secret itself, but
    the matched line around it can still say too much, and run folders may be served to others.
    """
    with tempfile.TemporaryDirectory(prefix="cq-secrets-") as tmp:
        report = Path(tmp) / "report.json"
        run(
            [
                "betterleaks",
                *args,
                "--config",
                str(config),
                "--redact",
                "--no-banner",
                "--exit-code",
                "0",
                "--report-format",
                "json",
                "--report-path",
                str(report),
            ],
            cwd,
            secret_output=True,
        )
        return [slim(f) for f in json.loads(report.read_text() or "[]")]


def slim(f: dict) -> dict:
    """The fields worth keeping: where and what kind, never the (already redacted) match."""
    return {
        "rule": f["RuleID"],
        "file": f["File"],
        "line": f["StartLine"],
        "commit": (f.get("Commit") or "")[:7],
        "date": (f.get("Date") or "")[:10],
        "fingerprint": f.get("Fingerprint", f"{f['File']}:{f['RuleID']}:{f['StartLine']}"),
    }


def scan_tree(ctx: Context) -> list[dict]:
    """The tracked files at the checkout's HEAD, extracted to a temp dir and scanned."""
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=ctx.checkout, capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        found = betterleaks(["dir", tmp], Path(tmp), gitleaks_config(ctx.settings))
    # Findings name the extracted files by their temp path (/private/var/… or /var/…); keep the repo part.
    marker = f"/{Path(tmp).name}/"
    for f in found:
        f["file"] = f["file"].split(marker, 1)[-1]
    return found


def scan_history(ctx: Context) -> list[dict]:
    """Every commit reachable from HEAD, reusing the cached result for the commits already scanned."""
    head = run(["git", "rev-parse", "HEAD"], ctx.checkout).strip()
    config = gitleaks_config(ctx.settings)
    cache_dir = ctx.settings.runs_dir / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = config_hash(config)
    cached = sorted(cache_dir.glob(f"betterleaks-history-{key}-*.json"), key=lambda p: p.stat().st_mtime)
    base: dict | None = None
    for path in reversed(cached):
        data = json.loads(path.read_text())
        is_ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", data["head"], head], cwd=ctx.checkout, check=False
        )
        if is_ancestor.returncode == 0:
            base = data
            break
    log_opts = [f"--log-opts={base['head']}..{head}"] if base else []
    if base and base["head"] == head:
        found: list[dict] = []
    else:
        found = betterleaks(["git", ".", *log_opts], ctx.checkout, config)
    merged = {f["fingerprint"]: f for f in [*(base["findings"] if base else []), *found]}
    findings = sorted(merged.values(), key=lambda f: (f["date"], f["file"]))
    (cache_dir / f"betterleaks-history-{key}-{head[:12]}.json").write_text(
        json.dumps({"head": head, "findings": findings})
    )
    return findings


def parse_trufflehog(out: str) -> list[dict]:
    """trufflehog's JSON lines reduced to safe fields; the raw secret and its redacted form are dropped."""
    rows: list[dict] = []
    for line in out.splitlines():
        if not line.startswith("{"):
            continue
        r = json.loads(line)
        git = r["SourceMetadata"]["Data"]["Git"]  # `trufflehog git` writes it for every finding
        rows.append(
            {
                "detector": r.get("DetectorName", "?"),
                # Which credential, as a truncated hash: counts distinct keys, never stored.
                "identity": hashlib.sha256((r.get("RawV2") or r.get("Raw") or "").encode()).hexdigest()[:12],
                "verified": bool(r.get("Verified")),
                "file": git.get("file", ""),
                "line": git.get("line"),
                "commit": (git.get("commit") or "")[:7],
                "date": (git.get("timestamp") or "")[:10],
                "unverifiable": bool(r.get("VerificationError")),
            }
        )
    return rows


def scan_verified(ctx: Context) -> list[dict]:
    """trufflehog over the whole history, with live verification; nothing it prints is stored."""
    out = run(
        # Only the measured revision's history (a worktree shares the repo's other refs), and a scan
        # error is a tool error, not a partial result.
        [
            "trufflehog",
            "git",
            f"file://{ctx.checkout}",
            "--branch",
            "HEAD",
            "--skip-additional-refs",
            "--fail-on-scan-errors",
            "--json",
            "--no-update",
            "--concurrency",
            "8",
        ],
        ctx.checkout,
        secret_output=True,
    )
    rows = parse_trufflehog(out)
    if rows and all(r["unverifiable"] for r in rows):
        # No key could be checked (no network, providers down): "0 live" would be a blind spot.
        raise ToolError(f"trufflehog could verify none of its {len(rows)} findings (network?)")
    unique = {(r["identity"], r["file"], r["commit"], r["line"]): r for r in rows}
    return sorted(unique.values(), key=lambda r: (not r["verified"], r["date"]))


DELETED = "deleted"


# Paths as the scanners print them: git C-quotes non-ASCII names (e.g. "®") unless told not to.
GIT_RAW_PATHS = ("git", "-c", "core.quotepath=off")


def path_events(checkout: Path, revisions: str = "HEAD") -> list[tuple[str, str, str, str]]:
    """Every rename and deletion on the first-parent line of ``revisions``, oldest first:
    (commit, status, old, new).

    First parent only: a merge then shows what it really changed against main, so a side branch's
    rename that the merge didn't keep is never applied.
    """
    events = []
    commit = ""
    # -z: NUL-separated fields, so a path with a tab or newline comes back raw, like the scanners print it.
    log = run(
        [
            *GIT_RAW_PATHS,
            "log",
            "-z",
            "--first-parent",
            "--reverse",
            "-M",
            "--diff-filter=RD",
            "--name-status",
            "--format=@%H",
            revisions,
        ],
        checkout,
    )
    fields = iter(log.split("\0"))
    for field in fields:
        token = field.lstrip("\n")
        if token.startswith("@"):
            commit = token[1:]
        elif token.startswith("R"):
            old, new = next(fields), next(fields)
            events.append((commit, "R", old, new))
        elif token.startswith("D"):
            old = next(fields)
            events.append((commit, "D", old, old))
    return events


def first_parent_position(checkout: Path, commit: str, order: dict[str, int]) -> tuple[int, str]:
    """Where a commit enters HEAD's first-parent line: its own position, or the position of the
    commit that joined its branch in, with that commit's sha ("" for a commit on the line itself).
    -1 when unknown (every event then applies)."""
    if not commit:
        return -1, ""
    found = next((n for sha, n in order.items() if sha.startswith(commit)), None)
    if found is not None:  # 0 is the root commit
        return found, ""
    # The descendants of the commit, oldest first; the first one on the first-parent line joined it in.
    descendants = run(["git", "rev-list", "--ancestry-path", "--reverse", f"{commit}..HEAD"], checkout).split()
    join = next((sha for sha in descendants if sha in order), "")
    return (order[join], join) if join else (-1, "")


def follow(path: str | None, moves: list[tuple[str, str, str]]) -> str | None:
    """Where ``path`` ends up after the (status, old, new) renames and deletions, in order; None once deleted."""
    for status, old, new in moves:
        if old == path:
            path = new if status == "R" else None
    return path


def head_paths(checkout: Path, found_at: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Where each (path, commit) is at HEAD, following only what happened to it after that commit,
    or "deleted".

    Secrets in history are reported at the path of the commit that added them, which a later
    move (a folder renamed, or moved under another) can leave pointing at nothing. Tracing
    from the finding's own commit keeps a path that was deleted and later reused by another file
    from being reported as unchanged.
    """
    line = run(["git", "rev-list", "--first-parent", "--reverse", "HEAD"], checkout).split()
    order = {sha: n for n, sha in enumerate(line)}
    events = [(order.get(commit, -1), status, old, new) for commit, status, old, new in path_events(checkout)]
    tracked = set(run([*GIT_RAW_PATHS, "ls-files", "-z"], checkout).split("\0"))
    result = {}
    for path, commit in found_at:
        start, join = first_parent_position(checkout, commit, order)
        # A side-branch commit: first what its branch did to the file before it was joined in.
        branch = path_events(checkout, f"{commit}..{join}^2") if join else []
        current = follow(path, [(status, old, new) for _, status, old, new in branch])
        current = follow(current, [(status, old, new) for n, status, old, new in events if n > start])
        result[(path, commit)] = current if current in tracked else DELETED
    return result


def collect(ctx: Context) -> dict:
    """Counts by rule for the tree and the history, which keys are live, and the findings (no values)."""
    require("betterleaks", "trufflehog")
    tree = scan_tree(ctx)
    history = scan_history(ctx)
    verified = scan_verified(ctx)
    by_rule: dict[str, int] = {}
    for f in history:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    # One row per credential, its newest occurrence, newest first: the cap counts keys, not places.
    newest: dict[str, dict] = {}
    for r in sorted((r for r in verified if r["verified"]), key=lambda r: r["date"]):
        newest[r["identity"]] = r
    live = sorted(newest.values(), key=lambda r: r["date"], reverse=True)
    places = {(f["file"], f["commit"]) for f in history[:120]} | {(r["file"], r["commit"]) for r in live[:40]}
    now = head_paths(ctx.checkout, places)
    return {
        "in_tree": len(tree),
        "in_history": len(history),
        "verified_live": len(live),
        "unverifiable": sum(1 for r in verified if r["unverifiable"]),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "tree_items": [{"file": f["file"], "line": f["line"], "code": f["rule"]} for f in tree[:60]],
        "items": [
            {
                "file": f["file"],
                "head_file": now[(f["file"], f["commit"])],
                "line": f["line"],
                "code": f["rule"],
                "commit": f["commit"],
                "date": f["date"],
            }
            for f in history[:120]
        ],
        "live_items": [
            {
                "file": r["file"],
                "head_file": now[(r["file"], r["commit"])],
                "line": r["line"],
                "code": r["detector"],
                "commit": r["commit"],
                "date": r["date"],
            }
            for r in live[:40]
        ],
    }
