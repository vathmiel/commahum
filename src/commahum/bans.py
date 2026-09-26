"""The style policy the rewriting obeys, read from the vendored humanizer-ru skill.

humanizer-ru is the source of truth for what counts as a trace of machine-written
Russian. Its machine-readable half is the HARD BANS table — one name and one
regular expression per construction — in the vendored
``scripts/humanizer_metrics/markers.py``. This module reads that table instead of
restating it, so the rules cannot drift from the revision the project pins.

The guard is deliberately one-sided: **a rewrite may remove a hard ban, never add
one.** A comment that already said «является» keeps it — the model is asked to do
better, but text the author wrote is not refused for being what it is. A rewrite
that brings a banned construction *in* is refused like any other rewrite that
fails the checks, is retried once, and is reported if it keeps failing.

Reading the table with :mod:`ast` rather than importing it is deliberate: the
module needs razdel and pymorphy3 for its morphological checks, and the rewriting
pipeline has no other use for them.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

__all__ = ["hard_bans", "introduced_bans", "markers_path"]

_ROOT = Path(__file__).resolve().parents[2]
_MARKERS = _ROOT / "vendor" / "humanizer-ru" / "scripts" / "humanizer_metrics" / "markers.py"


def markers_path() -> Path:
    """Where the vendored rule table lives."""
    return _MARKERS


@lru_cache(maxsize=1)
def hard_bans() -> tuple[tuple[str, re.Pattern], ...]:
    """The vendored HARD BANS as ``(name, compiled pattern)`` pairs.

    Fail-closed: a missing or unreadable table raises rather than silently
    policing nothing.
    """
    if not _MARKERS.is_file():
        raise RuntimeError(f"the vendored style rules are missing: {_MARKERS}")
    tree = ast.parse(_MARKERS.read_text(encoding="utf-8"), filename=str(_MARKERS))
    table = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "HARD_BANS":
            table = ast.literal_eval(node.value)
            break
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "HARD_BANS" for target in node.targets
        ):
            table = ast.literal_eval(node.value)
            break
    if not table:
        raise RuntimeError(f"no HARD_BANS table in {_MARKERS}")
    return tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in table)


def introduced_bans(before: str, after: str) -> tuple[str, ...]:
    """The hard bans ``after`` has that ``before`` did not.

    Bans already present in the original are not the rewrite's doing: they are the
    model's job to remove, not this guard's job to refuse.
    """
    already = {name for name, pattern in hard_bans() if pattern.search(before)}
    return tuple(
        name
        for name, pattern in hard_bans()
        if name not in already and pattern.search(after)
    )
