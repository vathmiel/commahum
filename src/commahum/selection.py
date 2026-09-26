"""Which comments may be rewritten, and what must survive verbatim.

Two protections live here.

Not every comment is prose. A `# nix-shell -p ...` line, a modeline, a bare
URL, a separator made of dashes — rewriting those is at best pointless and at
worst breaks tooling, so they are skipped and the skip reason is reported.

The model is also never handed the parts that carry meaning to a machine: the
`#` run, the `TODO:` word, the `/*` and `*/` delimiters. Those are split off
first and reassembled after, so a model cannot lose what it never received.
The model sees prose, and only prose. It never sees code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .comments import CommentSpan, find_comment_spans

__all__ = ["Candidate", "Selection", "prose", "select_candidates"]

#: `TODO:` and friends stay put; only what follows them is rewritten.
_MARKER_WORDS = re.compile(rb"(?:TODO|FIXME|HACK|XXX)\b[:!]?[ \t]*")

#: Comments that mean something to a tool, not to a reader.
_DIRECTIVE_PREFIXES = (
    b"nix-shell",
    b"shellcheck",
    b"spdx-",
    b"vim:",
    b"vi:",
    b"ex:",
    b"-*-",
)

_URL_SCHEME = re.compile(rb"^[a-z][a-z0-9+.\-]*://")
_TOKEN_EDGES = b"()[]<>,;:\"'`"
_PATH_BYTES = frozenset(b"/-_.~:@#%?&=+")


@dataclass(frozen=True)
class Candidate:
    """One comment offered for rewriting, split into preserved and rewritable parts.

    ``prefix`` and ``suffix`` are reassembled around whatever the model returns
    for ``body``; the model never sees them.
    """

    span: CommentSpan
    prefix: bytes
    body: bytes
    suffix: bytes = b""

    def assemble(self, body: bytes) -> bytes:
        """Return the full comment text with ``body`` substituted in."""
        return self.prefix + body + self.suffix


@dataclass(frozen=True)
class Selection:
    """The comments worth rewriting and the ones deliberately left alone.

    ``skipped`` holds ``(span, reason)`` pairs so a caller can report why a
    comment was left untouched.
    """

    candidates: tuple[Candidate, ...]
    skipped: tuple[tuple[CommentSpan, str], ...]


def select_candidates(source: bytes) -> Selection:
    """Split ``source``'s comments into candidates and skips."""
    candidates: list[Candidate] = []
    skipped: list[tuple[CommentSpan, str]] = []
    for span in find_comment_spans(source):
        candidate, reason = _classify(span)
        if candidate is None:
            skipped.append((span, reason))
        else:
            candidates.append(candidate)
    return Selection(tuple(candidates), tuple(skipped))


def prose(source: bytes) -> str:
    """Exactly the prose the model is sent, one comment per line.

    What the scanner grades and what the model was allowed to see have to be the
    same text: comment markers are stripped, code never appears, and the two
    callers that measure the result (`report`, `measure`) share this one function
    so that they cannot drift apart.
    """
    return "\n".join(
        candidate.body.decode("utf-8", "surrogateescape").replace("\n", " ")
        for candidate in select_candidates(source).candidates
    )


def _classify(span: CommentSpan) -> tuple[Candidate | None, str]:
    text = span.text
    if span.start_byte == 0 and text.startswith(b"#!"):
        return None, "shebang"

    if text.startswith(b"/*"):
        if len(text) < 4 or not text.endswith(b"*/"):
            return None, "unterminated"
        inner = text[2:-2]
        leading = inner[: len(inner) - len(inner.lstrip(b" \t"))]
        trailing = inner[len(inner.rstrip(b" \t")) :]
        prefix = b"/*" + leading
        suffix = trailing + b"*/"
        body = inner[len(leading) : len(inner) - len(trailing)]
    else:
        marker_run = len(text) - len(text.lstrip(b"#"))
        rest = text[marker_run:]
        separator = rest[: len(rest) - len(rest.lstrip(b" \t"))]
        prefix = b"#" * marker_run + separator
        suffix = b""
        body = rest[len(separator) :]

    if not body:
        return None, "empty"
    if body.lower().startswith(_DIRECTIVE_PREFIXES):
        return None, "directive"

    marker = _MARKER_WORDS.match(body)
    if marker:
        prefix += marker.group(0)
        body = body[len(marker.group(0)) :]
        if not body:
            return None, "empty"

    if not _has_letters(body) or _is_only_urls_and_paths(body):
        return None, "not prose"
    return Candidate(span, prefix, body, suffix), ""


def _has_letters(body: bytes) -> bool:
    return any(character.isalpha() for character in body.decode("utf-8", "surrogateescape"))


def _is_only_urls_and_paths(body: bytes) -> bool:
    tokens = body.split()
    return bool(tokens) and all(_is_url_or_path(token) for token in tokens)


def _is_url_or_path(token: bytes) -> bool:
    token = token.strip(_TOKEN_EDGES)
    if not token:
        return True
    if _URL_SCHEME.match(token):
        return True
    if b"/" not in token:
        return False
    # Non-ASCII bytes fall through: a word that merely contains a slash is prose.
    return all(byte in _PATH_BYTES or (byte < 0x80 and chr(byte).isalnum()) for byte in token)
