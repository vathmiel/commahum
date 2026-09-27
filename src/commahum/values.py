"""The technical-value guard: what a rewrite must not do to paths, links and numbers.

The rules already promise it — carrying numbers, dates, names and links over
without distortion is the humanizer-ru skill's own fact lock — and the scanner in
`report`/`measure` grades the result afterwards. This module is the deterministic
half the rewriting itself obeys: the technical values of the old comment body and
of the new one are compared before the rewrite is accepted, and a body that
drops, changes or invents one is refused like any other failing body — retried
once, then the original text stands and the reason is reported.

What counts is deliberately narrow: paths, URLs, hosts and digit-bearing tokens
(ports, counts, versions). Nothing here understands meaning, so nothing here can
promise a rewrite is true: a body with no such token passes however it is
reworded, and ordinary Russian prose must not trip on this — `и/или`,
`онлайн/оффлайн`, `когда-то`, `т. е.` carry no values. The guard is also blind
to intent: writing «пять» for «5», «1 000» for «1000», or re-formatting a date is
refused exactly like a real change, because a heuristic cannot tell them apart.
That error costs one retry and leaves the comment as it was, which is the safe
direction to be wrong in.
"""

from __future__ import annotations

import re
from collections import Counter

__all__ = ["technical_values", "value_drift"]

#: Where a path starts — an optional `~`, `.` or `..`, then a slash — and how it
#: runs on: to whitespace or a closing mark. The lookbehind is what keeps the
#: slash of `и/или`, of `онлайн/оффлайн` and of the `//` in a URL from starting
#: one. A colon is not part of a path, so anything URL-shaped has already been
#: taken by :data:`_URL`.
_PATH = re.compile(r"(?<![\w./:\-])(?:~|\.{1,2})?/[^\s()\[\]<>,;:\"'`«»„“|!?…—–]+")

#: Bare relative paths need ASCII segments on both sides of a slash; Cyrillic
#: slash prose (`и/или`, `онлайн/оффлайн`) does not match.
_BARE_PATH = re.compile(
    r"(?<![\w./:\-])[A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.\-]+)+"
)

#: `scheme://…`. The tail keeps a colon (a port or user info) and a query mark,
#: and stops at sentence punctuation.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s()\[\]<>,;\"'`«»„“|!…—–]+")

#: `host.tld` with an optional path, and `localhost` (no dot, still an address).
#: The TLD list is short on purpose: `т. е.` and `i.e.` must not look like hosts,
#: and the Cyrillic `рф` stays out for the same reason — the guard would rather
#: miss an address than mistake an abbreviation for one.
_TLDS = "ru|com|org|net|io|dev|tech|ai|su|local|internal|lan"
_HOST = re.compile(
    r"(?<![\w./:\-])(?:localhost|(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+(?:" + _TLDS + r"))\b"
    r"(?:/[A-Za-z0-9._~:@%+\-]*)*"
)

#: A number and whatever is glued to it: `3.11`, `2.24.9`, `0xff00ff`, the `16`
#: of `16-ядерный`. The hyphen is not part of the token, so `10-15` is two
#: numbers and compares equal to `от 10 до 15`.
_NUMBER = re.compile(r"\d+(?:\.\d+)*[A-Za-z0-9]*")

#: Sentence punctuation and a trailing slash for paths: `/etc/nixos/` is one
#: path, but a URL's trailing slash or query mark can change the resource.
_EDGES = ".,;:!?/"


def technical_values(text: str) -> tuple[str, ...]:
    """The technical values of ``text``, sorted, duplicates kept.

    Paths, URLs and hosts are matched first, and a number inside one of them is
    not listed separately: a rewritten URL reads as one change, not four.
    """
    spans: list[tuple[int, int]] = []
    values: list[str] = []
    for pattern in (_URL, _PATH, _HOST, _BARE_PATH):
        for match in pattern.finditer(text):
            if _within(match.span(), spans):
                continue
            spans.append(match.span())
            edges = _EDGES.rstrip("/?") if pattern is _URL else _EDGES
            values.append(match.group(0).rstrip(edges))
    for match in _NUMBER.finditer(text):
        if _within(match.span(), spans):
            continue
        values.append(match.group(0))
    return tuple(sorted(values))


def value_drift(before: str, after: str) -> tuple[str, ...]:
    """What the rewrite did to the values: ``lost …`` and ``gained …``, or ``()``.

    Both sides compare as multisets: losing one of two identical numbers is a
    loss like any other. A body that is kept as it was drifts by nothing.
    """
    old = Counter(technical_values(before))
    new = Counter(technical_values(after))
    return tuple(
        [f"lost {value}" for value in sorted((old - new).elements())]
        + [f"gained {value}" for value in sorted((new - old).elements())]
    )


def _within(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(begin <= start and end <= stop for begin, stop in spans)
