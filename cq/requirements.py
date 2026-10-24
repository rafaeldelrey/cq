"""A requirements.txt app: a Python target that lists its dependencies in requirements files.

Such an app has a ``requirements.txt`` and no ``pyproject.toml``, so no uv project and no lock. The
tests, vulnerabilities and freshness collectors read its dependencies from these files instead.
"""

import fnmatch
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from packaging.markers import Marker

from cq.config import PYPROJECT

REQUIREMENTS = "requirements.txt"
# Test tools usually live in a dev file; the first of these that exists joins requirements.txt.
DEV_FILES = ("requirements-dev.txt", "requirements/dev.txt")
SKIP_NO_MANIFEST = "no pyproject.toml or requirements.txt: the app declares no dependencies cq can read"
SKIP_DYNAMIC = "dynamic dependencies and no uv.lock: resolving them would build the project in the repo"
SKIP_NEEDS_BUILD = "no uv.lock, and resolving the dependencies needs a build, which cq doesn't run"

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


def applies(line: str) -> bool:
    """Whether a line's environment marker (``; sys_platform == "linux"``) holds for the Python running cq.

    A file can pin a package differently per platform or Python version; only the pin that applies
    here is measured. A marker packaging can't read raises, so the collector reports a tool error.
    """
    _, _, marker = line.partition(";")
    marker = LINE_OPTION.sub("", f" {marker}").strip()
    return not marker or Marker(marker).evaluate()


def parse_line(line: str) -> Requirement | None:
    """One line as a requirement; None for an option line (``--index-url``, ``-c``) or one whose marker excludes it."""
    editable = EDITABLE.match(line)
    if editable:
        return Requirement(url_name(editable["target"]), None)
    if line.startswith("-"):
        return None
    m = NAME.match(line)
    if not m or "://" in line.split("@")[0] or line.startswith((".", "/")):
        return Requirement(url_name(line), None)  # a bare URL or path: never a pin
    if not applies(line):
        return None
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


def toml_table(data: dict, *keys: str) -> dict:
    """A nested TOML table, or an empty one where any level is missing."""
    for key in keys:
        value = data.get(key)
        data = value if isinstance(value, dict) else {}
    return data


def project_data(app_dir: Path) -> dict:
    """The app's pyproject.toml, parsed."""
    return tomllib.loads((app_dir / PYPROJECT).read_text())


def has_dev_group(app_dir: Path) -> bool:
    """Whether the app has a ``dev`` dependency group, as ``[dependency-groups]`` or uv's older
    ``[tool.uv] dev-dependencies``, which uv reads as the same group."""
    data = project_data(app_dir)
    return "dev" in toml_table(data, "dependency-groups") or bool(legacy_dev(data))


def legacy_dev(data: dict) -> list:
    """uv's older ``[tool.uv] dev-dependencies``: part of the ``dev`` group."""
    value = toml_table(data, "tool", "uv").get("dev-dependencies", [])
    return value if isinstance(value, list) else []


def project_requirements(app_dir: Path) -> list[Requirement]:
    """An unlocked uv app's own requirements: ``[project].dependencies``, plus its ``dev`` group or extra.

    The same line rules as requirements files (markers evaluated, names normalized). A group's
    ``include-group`` tables are not followed (#60).
    """
    data = project_data(app_dir)
    project = toml_table(data, "project")
    lines = [
        *project.get("dependencies", []),
        *toml_table(project, "optional-dependencies").get("dev", []),
        *toml_table(data, "dependency-groups").get("dev", []),
        *legacy_dev(data),
    ]
    local = local_sources(sources_of(app_dir, data))
    return [req for line in lines if isinstance(line, str) and (req := parse_line(line)) and req.name not in local]


def workspace_root(app_dir: Path) -> Path | None:
    """The root of the uv workspace the app belongs to: the nearest folder above it whose pyproject.toml
    defines a workspace, when the app matches its ``members`` and none of its ``exclude`` patterns."""
    app = app_dir.resolve()
    for folder in app.parents:
        pyproject = folder / PYPROJECT
        workspace = (
            toml_table(tomllib.loads(pyproject.read_text()), "tool", "uv", "workspace") if pyproject.exists() else {}
        )
        if workspace:
            rel = app.relative_to(folder).as_posix()
            member = any(glob_match(rel, g) for g in workspace.get("members", []))
            return folder if member and not any(glob_match(rel, g) for g in workspace.get("exclude", [])) else None
    return None


def glob_match(path: str, pattern: str) -> bool:
    """Whether a relative path matches a workspace glob: ``*`` stays within one folder, ``**`` spans any number."""
    return segments_match(path.split("/"), pattern.strip("/").split("/"))


def segments_match(parts: list[str], globs: list[str]) -> bool:
    """The path's segments against the glob's, ``**`` matching zero or more of them."""
    if not globs:
        return not parts
    if globs[0] == "**":
        return any(segments_match(parts[i:], globs[1:]) for i in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], globs[0]) and segments_match(parts[1:], globs[1:])


def sources_of(app_dir: Path, data: dict) -> dict:
    """The app's ``[tool.uv.sources]``, over its workspace root's: a member inherits the root's, and its own win.

    Names are normalized first, so ``Helper_Lib`` in one and ``helper-lib`` in the other are the same.
    """
    root = workspace_root(app_dir)
    inherited = toml_table(project_data(root), "tool", "uv", "sources") if root else {}
    own_sources = toml_table(data, "tool", "uv", "sources")
    return {canonical(name): source for table in (inherited, own_sources) for name, source in table.items()}


def local_sources(sources: dict, kinds: frozenset[str] = frozenset({"path", "git", "url", "workspace"})) -> set[str]:
    """The packages ``sources`` takes from one of ``kinds``: by default, any source that isn't an index."""
    local = set()
    for name, source in sources.items():
        entries = source if isinstance(source, list) else [source]
        if any(isinstance(e, dict) and kinds & e.keys() and source_applies(e) for e in entries):
            local.add(canonical(name))
    return local


SELECTED = "dev"  # the only extra and dependency group cq reads (and installs)


def source_applies(entry: dict) -> bool:
    """Whether a ``[tool.uv.sources]`` entry applies to what cq measures.

    Its marker must hold for the Python running cq, and its extra or group, if it names one, must be
    the ``dev`` one cq selects.
    """
    marker = entry.get("marker")
    scoped = [entry[key] for key in ("extra", "group") if key in entry]
    return (not marker or Marker(marker).evaluate()) and all(name == SELECTED for name in scoped)


def dynamic_dependencies(app_dir: Path) -> bool:
    """Whether the app's dependencies are computed by its build backend (``dynamic``), not written out.

    Resolving them means building the project, which writes ``<name>.egg-info/`` into the repo.
    """
    return "dependencies" in toml_table(project_data(app_dir), "project").get("dynamic", [])
