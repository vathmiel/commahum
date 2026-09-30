"""Unified-diff rendering.

The diff is what a human reviews before accepting a rewrite, so it renders the
original and rewritten bytes as text lines and stays lossless: re-encoding the
diff lines with ``surrogateescape`` gives the original bytes back, even for a
file that is not valid UTF-8.
"""

from __future__ import annotations

import difflib

__all__ = ["render_diff"]


def render_diff(original: bytes, rewritten: bytes, path: str) -> str:
    """Return a unified diff of ``original`` against ``rewritten``, or ``""``.

    ``path`` only names the file in the headers; nothing is read from disk.
    """
    if original == rewritten:
        return ""
    diff = difflib.unified_diff(
        _lines(original),
        _lines(rewritten),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in diff
    )


def _lines(data: bytes) -> list[str]:
    """Decode ``data`` into newline-terminated lines without losing any byte."""
    # Unified diffs count LF-delimited lines. Unicode separators and bare CRs
    # are bytes inside a line, not extra lines in a patch's hunk counts.
    parts = data.decode("utf-8", "surrogateescape").split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
