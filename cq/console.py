"""cq's output: a command's results on stdout, progress and notes on stderr, and its questions.

The one place cq prints, so the streams are chosen once and a future ``--quiet``, ``--json`` or colour
is a change here. Everything else calls these instead of ``print``.
"""

# aislop-ignore-file ai-slop/python-print-debug -- the console is where cq prints, on purpose

import sys


def out(text: str = "") -> None:
    """A line of a command's result, on stdout."""
    print(text)


def note(text: str = "", end: str = "\n") -> None:
    """Progress or a note for the user, on stderr, written at once."""
    print(text, end=end, flush=True, file=sys.stderr)


def flush() -> None:
    """Write out what cq printed so far, before a child process writes to the same terminal."""
    sys.stdout.flush()


def ask(prompt: str) -> str:
    """The user's answer to a question, read from the terminal."""
    return input(prompt)


def is_terminal() -> bool:
    """Whether someone can answer a question: stdin is a terminal, not a pipe or CI."""
    return sys.stdin.isatty()


def confirm(question: str, *, assume_yes: bool) -> bool:
    """A y/N answer; no answer without a terminal, unless ``assume_yes``."""
    if assume_yes:
        return True
    if not is_terminal():
        out(f"{question} [y/N] no (not a terminal: pass --yes)")
        return False
    return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")
