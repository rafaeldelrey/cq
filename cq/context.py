"""What a collector gets to work with."""

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cq.config import Settings, Target


@lru_cache(maxsize=256)
def _lines(path: Path) -> tuple[str, ...]:
    try:
        return tuple(path.read_text(errors="replace").splitlines())
    except OSError:  # a file the tool named but the checkout doesn't have: no fingerprint
        return ()


@dataclass(frozen=True)
class Context:
    """One target, measured in one checkout, with a directory for the tool's raw output."""

    target: Target
    checkout: Path  # the repo itself, or a temporary worktree at another commit
    raw_dir: Path  # gitignored: runs/<run_id>/<target>/
    settings: Settings
    full: bool = False  # the gate: every item, so a new finding never hides behind a report cap

    @property
    def app_dir(self) -> Path:
        """The target's app directory in this checkout."""
        return self.target.app_dir(self.checkout)

    @property
    def sources(self) -> list[Path]:
        """The target's production paths in this checkout."""
        return self.target.source_paths(self.checkout)

    def rel(self, path: Path | str, base: Path | None = None) -> str:
        """A path relative to the checkout, so snapshots from a worktree read like the repo's.

        A relative ``path`` is taken relative to ``base``: the directory the tool ran in, which
        is the app directory unless the collector says otherwise.
        """
        p = Path(path)
        if not p.is_absolute():
            p = ((base or self.app_dir) / p).resolve()
        try:
            return p.relative_to(self.checkout.resolve()).as_posix()
        except ValueError:
            return p.as_posix()

    def cap(self, items: list, limit: int) -> list:
        """The first ``limit`` items for a report, or all of them in a gate run."""
        return items if self.full else items[:limit]

    def fingerprint(self, file: str, line: int | None) -> str:
        """A short hash of a finding's source line: who it is, whatever lines shift above it.

        A hash, not the text, so nothing the line says lands in the LAN-served run folder.
        """
        lines = _lines(self.checkout / file)
        text = lines[line - 1].strip() if line and 0 < line <= len(lines) else ""
        return hashlib.sha256(text.encode()).hexdigest()[:10] if text else ""

    def with_fingerprints(self, items: list[dict]) -> list[dict]:
        """The items, each with ``at``: its source line's fingerprint."""
        return [i | {"at": self.fingerprint(i["file"], i["line"])} for i in items]
