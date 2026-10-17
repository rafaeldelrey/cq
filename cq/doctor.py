"""``cq doctor`` and ``cq setup``: which measuring tools are on this machine, and installing the rest.

The Python tools come with cq; the npx and uvx tools are fetched on first use. What remains are
system binaries: Homebrew installs them, and qlty comes from its GitHub release at the version this
cq release pins, checked against its checksum and its build attestation before it is installed.
"""

import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cq import plugin
from cq.config import PINS
from cq.proc import run


@dataclass(frozen=True)
class Tool:
    """A system binary cq runs."""

    name: str
    version_flag: str
    formula: str  # the Homebrew formula that installs it ("" for qlty, installed from its release)
    needed_for: str
    optional: bool = False  # needed only by some repos (hadolint: a Dockerfile; trivy: an image)


TOOLS = (
    Tool("git", "--version", "git", "everything"),
    Tool("uv", "--version", "uv", "running each app's own environment, pyscn"),
    Tool("npx", "--version", "node", "jscpd, knip, aislop, fallow, cspell, the TypeScript tools"),
    Tool("qlty", "--version", "", "radarlint, smells, workflow security, the gate's line checks"),
    Tool("betterleaks", "version", "betterleaks", "secrets in the tree and the history"),
    Tool("trufflehog", "--version", "trufflehog", "which committed keys are still live"),
    Tool("vale", "--version", "vale", "AI-prose and stub language"),
    Tool("shellcheck", "--version", "shellcheck", "shell scripts"),
    Tool("actionlint", "--version", "actionlint", "GitHub workflows"),
    Tool("lychee", "--version", "lychee", "broken links in the docs"),
    Tool("hadolint", "--version", "hadolint", "Dockerfiles", optional=True),
    Tool("trivy", "--version", "trivy", "published images", optional=True),
)
QLTY_VERSION = re.compile(r"qlty (\S+)")
QLTY_BIN = Path.home() / ".local" / "bin" / "qlty"


@dataclass(frozen=True)
class Check:
    """One tool's state on this machine."""

    tool: Tool
    found: str  # the version line, or "" when missing
    ok: bool
    fix: str  # the command that installs or fixes it


def version_line(name: str, flag: str) -> str:
    """The first line a tool prints for its version, or "" when it isn't on the PATH."""
    path = shutil.which(name)
    if path is None:
        return ""
    try:
        proc = subprocess.run([path, flag], capture_output=True, text=True, check=False, timeout=30)
    except subprocess.TimeoutExpired:  # a shim that prompts or hangs: installed, version unknown
        return "unknown"
    text = (proc.stdout or proc.stderr).strip()
    return text.splitlines()[0] if text else "unknown"


def check(tool: Tool) -> Check:
    """Whether ``tool`` is installed, and at the pinned version for qlty."""
    found = version_line(tool.name, tool.version_flag)
    if tool.name == "qlty":
        m = QLTY_VERSION.search(found)
        ok = bool(m) and m.group(1) == PINS["qlty"]
        return Check(tool, found, ok, "cq setup" if not ok else "")
    return Check(tool, found, bool(found), "" if found else f"brew install {tool.formula}")


PLUGIN_LABEL = "claude plugin"


def plugin_line(width: int, folder: Path | None) -> str:
    """cq's Claude Code plugin, for the repo ``folder`` (default: the current one) is in: optional, never a failure."""
    ok, found, fix = plugin.status(folder or Path.cwd())
    line = f"{'ok ' if ok else '-- '} {PLUGIN_LABEL:<{width}}  {found}"
    return f"{line}\n      for /code-quality and /setup-cq: {fix}" if fix else line


def doctor(folder: Path | None = None) -> int:
    """Print every tool's state; exit 1 when a required one is missing or qlty is off its pin.

    ``folder`` (default: the current one) is the repo the plugin line is about.
    """
    checks = [check(t) for t in TOOLS]
    width = max(len(PLUGIN_LABEL), *(len(t.name) for t in TOOLS))
    for c in checks:
        mark = "ok " if c.ok else ("-- " if c.tool.optional else "XX ")
        state = c.found or "not installed"
        if c.tool.name == "qlty" and not c.ok:
            state = f"{state} (cq pins {PINS['qlty']})"
        line = f"{mark} {c.tool.name:<{width}}  {state}"
        print(f"{line}\n      for {c.tool.needed_for}: {c.fix}" if c.fix else line)
    print(plugin_line(width, folder))
    missing = [c for c in checks if not c.ok and not c.tool.optional]
    if missing:
        print(f"\n{len(missing)} required tool(s) missing or off their pin: `cq setup` installs them.")
        return 1
    print("\nEverything cq needs is installed.")
    return 0


def qlty_triple() -> str:
    """qlty's release name for this machine; a platform qlty doesn't build for stops setup."""
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "AMD64": "x86_64"}.get(platform.machine())
    system = {"Darwin": "apple-darwin", "Linux": "unknown-linux-gnu"}.get(platform.system())
    if machine is None or system is None:
        raise SystemExit(f"qlty has no release for {platform.machine()}/{platform.system()}: install it by hand")
    return f"{machine}-{system}"


def install_qlty(version: str, dest: Path = QLTY_BIN) -> None:
    """qlty from its GitHub release: checksum and attestation checked before the binary is copied."""
    triple = qlty_triple()
    archive = f"qlty-{triple}.tar.xz"
    with tempfile.TemporaryDirectory(prefix="cq-qlty-") as tmp:
        work = Path(tmp)
        run(["gh", "release", "download", f"v{version}", "--repo", "qltysh/qlty", "--pattern", f"{archive}*"], work)
        run(["shasum", "-a", "256", "-c", f"{archive}.sha256"], work)
        run(["gh", "attestation", "verify", "--owner", "qltysh", archive], work)
        with tarfile.open(work / archive) as tar:
            tar.extractall(work, filter="data")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(work / f"qlty-{triple}" / "qlty", dest)
        dest.chmod(0o755)


def confirm(question: str, *, assume_yes: bool) -> bool:
    """A y/N answer; no answer without a terminal, unless ``assume_yes``."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{question} [y/N] no (not a terminal: pass --yes)")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def plan(checks: list[Check]) -> tuple[list[str], bool]:
    """The Homebrew formulas to install, and whether qlty needs installing.

    Optional tools stay out: a repo with no Dockerfile or image never needs hadolint or Trivy, and
    doctor names the command for anyone who does.
    """
    formulas = [c.tool.formula for c in checks if not c.ok and c.tool.formula and not c.tool.optional]
    qlty_needed = any(c.tool.name == "qlty" and not c.ok for c in checks)
    if qlty_needed and shutil.which("gh") is None:
        formulas.append("gh")  # qlty's download and attestation check go through the GitHub CLI
    return formulas, qlty_needed


def show(formulas: list[str], qlty_needed: bool) -> None:
    """Print what setup will do."""
    print("cq setup will:")
    if formulas:
        print(f"  brew install {' '.join(formulas)}")
    if qlty_needed:
        print(f"  install qlty {PINS['qlty']} from github.com/qltysh/qlty (checksum + attestation) to {QLTY_BIN}")


def apply(formulas: list[str], qlty_needed: bool) -> int:
    """Install the plan; 1 when Homebrew is needed and missing."""
    if formulas:
        if shutil.which("brew") is None:
            print("Homebrew is not installed: see https://brew.sh, then run `cq setup` again.")
            return 1
        # Not through cq.proc.run: brew's progress goes straight to the terminal, as it would by hand.
        subprocess.run(["brew", "install", *formulas], check=True)
    if qlty_needed:
        install_qlty(PINS["qlty"])
        return shadowed_qlty(shutil.which("qlty"))
    return 0


def shadowed_qlty(found: str | None) -> int:
    """1, with what to do, when the qlty on the PATH isn't the one setup just installed."""
    if found is None:
        print(f"qlty is in {QLTY_BIN.parent}, which is not on your PATH: add it.")
        return 1
    if Path(found).resolve() != QLTY_BIN.resolve():
        print(f"Another qlty comes first on your PATH ({found}): remove it, or put {QLTY_BIN.parent} before it.")
        return 1
    return 0


def setup(*, assume_yes: bool = False) -> int:
    """Install what doctor reports missing, after showing the plan."""
    formulas, qlty_needed = plan([check(t) for t in TOOLS])
    if not formulas and not qlty_needed:
        print("Nothing to install.")
        return doctor()
    show(formulas, qlty_needed)
    if not confirm("Go ahead?", assume_yes=assume_yes) or apply(formulas, qlty_needed):
        return 1
    print()
    return doctor()
