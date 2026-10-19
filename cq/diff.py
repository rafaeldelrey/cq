"""What a branch changed, from git: the files, the renames, and the lines on each side."""

import re

from cq.runner import git

HUNK_SIDES = re.compile(r"^@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? \+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@")


def diff_status(base_sha: str) -> tuple[list[str], list[str], dict[str, str]]:
    """What the branch did since ``base_sha``: files on HEAD it added or changed, every path it
    touched (deletions and rename sources too), and its renames (old path → new path)."""
    changed: list[str] = []
    touched: list[str] = []
    renames: dict[str, str] = {}
    for line in git("diff", "--name-status", "-M", base_sha, "HEAD").splitlines():
        status, *paths = line.split("\t")
        touched += paths
        if status.startswith("R"):
            renames[paths[0]] = paths[1]
        if status[0] in "ACMR":
            changed.append(paths[-1])
    return changed, touched, renames


def diff_lines(base_sha: str) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """(the lines each file has on HEAD that the branch added or changed, the lines each file had on
    the base that the branch removed or changed), by HEAD path and base path."""
    added: dict[str, set[int]] = {}
    removed: dict[str, set[int]] = {}
    old_path = new_path = ""
    for line in git("diff", "--unified=0", "--no-color", base_sha, "HEAD").splitlines():
        if line.startswith("--- "):
            old_path = side_path(line, "--- a/")
        elif line.startswith("+++ "):
            new_path = side_path(line, "+++ b/")
        elif m := HUNK_SIDES.match(line):
            note_lines(added, new_path, int(m["new"]), int(m["new_count"] or 1))
            note_lines(removed, old_path, int(m["old"]), int(m["old_count"] or 1))
    return added, removed


def side_path(header: str, prefix: str) -> str:
    """The path a ``---``/``+++`` header names, or "" for /dev/null (a file added or deleted)."""
    return header[len(prefix) :] if header.startswith(prefix) else ""


def note_lines(lines: dict[str, set[int]], path: str, start: int, count: int) -> None:
    """Add a hunk's ``count`` lines from ``start`` to ``path``'s set; a side with no path has none."""
    if path:
        lines.setdefault(path, set()).update(range(start, start + count))


def changed_lines(base_sha: str) -> dict[str, set[int]]:
    """The lines each file has on HEAD that the branch added or changed."""
    return diff_lines(base_sha)[0]


def overlaps(row: dict, lines: dict[str, set[int]]) -> bool:
    """Whether a finding's span covers a line the branch changed."""
    start = row.get("start_line") or row.get("line") or 0
    end = row.get("end_line") or start
    return any(n in lines.get(row["file"], set()) for n in range(start, end + 1))
