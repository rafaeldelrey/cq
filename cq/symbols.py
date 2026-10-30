"""Which Python functions and classes a branch left as they were: the same syntax tree on the base
and on HEAD once docstrings are set aside. Comments never reach the tree, so an edit to a docstring
or a comment leaves a symbol unchanged; the gate then doesn't count the symbol's old findings as new.
"""

import ast
from collections.abc import Iterator

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
Def = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
Span = tuple[int, int]


def unchanged_spans(base: str | bytes, head: str | bytes) -> list[Span]:
    """HEAD's line spans of the symbols whose tree, docstrings aside, is the same in both sources.

    A source that doesn't parse on either side has none: its lines keep the plain line rule.
    """
    before, after = symbols(base), symbols(head)
    if before is None or after is None:
        return []
    return sorted(after[name][0] for name in after.keys() & before.keys() if after[name][1] == before[name][1])


def symbols(source: str | bytes) -> dict[str, tuple[Span, str]] | None:
    """Each function, method and class by qualified name: its span (decorators included) and its
    tree without docstrings. None when the source doesn't parse, its declared encoding included
    (bytes are decoded the way Python would). A name defined twice is left out: nothing says which
    of its definitions is which."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):  # ValueError: a null byte. Either way there is no tree to compare
        return None
    strip_docstrings(tree)
    found: dict[str, tuple[Span, str]] = {}
    twice: set[str] = set()
    for name, node in definitions(tree, ""):
        if name in found:
            twice.add(name)
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        found[name] = ((start, node.end_lineno or node.lineno), ast.dump(node))
    return {name: v for name, v in found.items() if name not in twice}


def definitions(node: ast.AST, prefix: str) -> Iterator[tuple[str, Def]]:
    """The functions and classes under ``node``, with names qualified by the ones they sit in."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, DEFS):
            name = f"{prefix}{child.name}"
            yield name, child
            yield from definitions(child, f"{name}.")
        else:
            yield from definitions(child, prefix)


def strip_docstrings(tree: ast.AST) -> None:
    """Drop the docstring from the module and from every function and class, in place."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, *DEFS)) and node.body and is_docstring(node.body[0]):
            node.body = node.body[1:]


def is_docstring(statement: ast.stmt) -> bool:
    """Whether a statement is a bare string: a docstring when it opens a body."""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def outside(lines: dict[str, set[int]], spans: dict[str, list[Span]]) -> dict[str, set[int]]:
    """The changed lines without those inside the given spans; a file left with none drops out."""
    kept: dict[str, set[int]] = {}
    for path, numbers in lines.items():
        rest = {n for n in numbers if not inside(n, spans.get(path, []))}
        if rest:
            kept[path] = rest
    return kept


def inside(line: int, spans: list[Span]) -> bool:
    """Whether a line falls in one of the spans."""
    return any(start <= line <= end for start, end in spans)


def covered(first: int, last: int, spans: list[Span]) -> bool:
    """Whether the lines ``first`` to ``last`` all fall in one of the spans."""
    return any(start <= first and last <= end for start, end in spans)
