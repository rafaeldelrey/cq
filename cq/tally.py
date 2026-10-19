"""Counting findings by a name: the ``by_rule`` / ``by_kind`` tables every collector reports."""

from collections import Counter
from collections.abc import Iterable


def ranked(names: Iterable[str]) -> dict[str, int]:
    """How often each name occurs, the most frequent first (ties in the order they first appear)."""
    return dict(Counter(names).most_common())
