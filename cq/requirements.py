"""A requirements.txt app: a Python target that lists its dependencies in requirements files.

Such an app has a ``requirements.txt`` and no ``pyproject.toml``, so no uv project and no lock. The
tests, vulnerabilities and freshness collectors read its dependencies from these files instead.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cq.config import PYPROJECT

REQUIREMENTS = "requirements.txt"
# Test tools usually live in a dev file; the first of these that exists joins requirements.txt.
DEV_FILES = ("requirements-dev.txt", "requirements/dev.txt")
SKIP_NO_MANIFEST = "no pyproject.toml or requirements.txt: the app declares no dependencies cq can read"

INCLUDE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)(?P<path>\S+)")
EDITABLE = re.compile(r"^(?:-e|--editable)(?:\s+|=)(?P<target>\S+)")
EGG = re.compile(r"#egg=(?P<name>[\w.\-]+)")
NAME = re.compile(r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?\s*(?P<spec>[^;]*)")
PIN = re.compile(r"^===?\s*(?P<version>[^\s,*]+)$")
# Per-requirement options (`--hash=…`, from pip-compile --generate-hashes) follow the version.
LINE_OPTION = re.compile(r"\s--?[a-z][\w-]*(?:[=\s]\S+)?")
# A `#` starts a comment at the line's start or after whitespace; `#egg=` inside a URL does not.
COMMENT = re.compile(r"(^|\s)#.*$")


@dataclass(frozen=True)
class Requirement:
    """One dependency line: the package, and its exact pin when the line has one."""

    name: str
    version: str | None  # None: unpinned (a range, a bare name, a URL or an editable path)


def is_requirements_app(app_dir: Path) -> bool:
    """Whether the app is a requirements.txt app rather than a uv project."""
    return (app_dir / REQUIREMENTS).exists() and not (app_dir / PYPROJECT).exists()


def files(app_dir: Path) -> list[Path]:
    """requirements.txt, then the first dev requirements file that exists."""
    dev = next((app_dir / name for name in DEV_FILES if (app_dir / name).exists()), None)
    return [app_dir / REQUIREMENTS, *([dev] if dev else [])]


def logical_lines(text: str) -> list[str]:
    """The file's lines with comments removed and backslash continuations joined."""
    lines, pending = [], ""
    for raw in text.splitlines():
        line = COMMENT.sub("", raw).rstrip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append((pending + line).strip())
        pending = ""
    lines.append(pending.strip())
    return [line for line in lines if line]


def url_name(target: str) -> str:
    """A name for a URL or path requirement: its ``#egg=`` name, else the last part of its path.

    Never the URL itself, which can carry a username and password before its host.
    """
    egg = EGG.search(target)
    if egg:
        return egg["name"]
    path = urlsplit(target).path if "://" in target else re.split(r"[?#]", target)[0]
    return path.rstrip("/").rsplit("/", 1)[-1].split("@")[0] or "(url)"


def parse_line(line: str) -> Requirement | None:
    """One line as a requirement; None for an option line that names no package (``--index-url``, ``-c``)."""
    editable = EDITABLE.match(line)
    if editable:
        return Requirement(url_name(editable["target"]), None)
    if line.startswith("-"):
        return None
    m = NAME.match(line)
    if not m or "://" in line.split("@")[0] or line.startswith((".", "/")):
        return Requirement(url_name(line), None)  # a bare URL or path: never a pin
    pin = PIN.match(LINE_OPTION.sub("", f" {m['spec']}").strip())
    return Requirement(canonical(m["name"]), pin["version"] if pin else None)


def canonical(name: str) -> str:
    """A package name as PyPI compares them (PEP 503): ``Foo_Bar`` and ``foo-bar`` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(path: Path, seen: frozenset[Path] = frozenset()) -> list[Requirement]:
    """The requirements in a file, following its ``-r`` includes relative to it."""
    resolved = path.resolve()
    if resolved in seen:
        return []
    found = []
    for line in logical_lines(path.read_text()):
        include = INCLUDE.match(line)
        if include:
            found += parse(path.parent / include["path"], seen | {resolved})
            continue
        req = parse_line(line)
        if req:
            found.append(req)
    return found
