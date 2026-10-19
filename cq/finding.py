"""One finding: the shape every tool's results are turned into, and how it is keyed.

Each finding has the same fields whatever tool produced it:

    target, tool, rule, severity (error | warning | info), file, line, symbol, message, key, new

``key`` identifies the finding across runs without its line number (lines shift when code above
moves), so "new since the previous snapshot" means a problem that did not exist before, not one
that moved. A key is part of the history's meaning: changing how one is built makes every old
finding of that tool look new, so it changes only with a CHANGELOG line.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

Finding = dict


@dataclass(frozen=True)
class Source:
    """Where findings come from: one tool's result in one target."""

    target: str
    tool: str

    def finding(
        self,
        rule: str,
        severity: str,
        message: str,
        file: str = "",
        line: int | None = None,
        *,
        symbol: str = "",
        **extra: object,
    ) -> Finding:
        """One finding from this source, keyed by its ``symbol`` or, without one, its message."""
        key = f"{self.target}|{self.tool}|{rule}|{file}|{symbol or message}"
        return {
            "target": self.target,
            "tool": self.tool,
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


def file_of(location: str) -> str:
    """``path/file.py:12`` → ``path/file.py``."""
    return location.rsplit(":", 1)[0]


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
