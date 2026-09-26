"""Byte-range splicing of rewritten comment text.

A rewrite is a ``(range, text)`` pair where the range came from the parser. The
splice is an explicit range replacement, never a search and replace, so a
rewrite cannot land anywhere the parser did not point at.

Every rule in :func:`apply_rewrites` exists to keep one promise: only comment
bytes change. A rewrite that would break that promise is refused rather than
repaired.
"""

from __future__ import annotations

from dataclasses import dataclass

from .comments import CommentError, CommentSpan, find_comment_spans

__all__ = ["Rewrite", "apply_rewrites"]


@dataclass(frozen=True)
class Rewrite:
    """A replacement comment text for one comment range."""

    span: CommentSpan
    text: bytes


def apply_rewrites(source: bytes, rewrites) -> bytes:
    """Return ``source`` with each rewrite's comment text spliced in.

    Raises :class:`~commahum.comments.CommentError` if a rewrite would leave the
    comment, change its style, overlap another rewrite, or come from another
    version of the file.
    """
    if not isinstance(source, (bytes, bytearray, memoryview)):
        raise TypeError(f"source must be bytes, not {type(source).__name__}")
    source = bytes(source)
    rewrites = list(rewrites)
    if not rewrites:
        return source

    ordered = sorted(rewrites, key=lambda rewrite: rewrite.span.start_byte)
    _reject_overlaps(ordered)
    comments = find_comment_spans(source)
    for rewrite in ordered:
        _check_rewrite(rewrite, source, comments)

    out = bytearray()
    position = 0
    for rewrite in ordered:
        out += source[position : rewrite.span.start_byte]
        out += rewrite.text
        position = rewrite.span.end_byte
    out += source[position:]
    return bytes(out)


def _reject_overlaps(ordered) -> None:
    previous_end = -1
    for rewrite in ordered:
        if rewrite.span.start_byte < previous_end:
            raise CommentError(
                f"rewrites overlap at byte {rewrite.span.start_byte}; "
                "one comment can only be rewritten once"
            )
        previous_end = rewrite.span.end_byte


def _check_rewrite(rewrite, source: bytes, comments) -> None:
    span = rewrite.span
    text = rewrite.text
    if not isinstance(text, (bytes, bytearray, memoryview)):
        raise TypeError(f"rewrite text must be bytes, not {type(text).__name__}")

    if span.source != source:
        raise CommentError(
            f"rewrite range {span.start_byte}:{span.end_byte} comes from a different source"
        )
    if comment_for(comments, span) is None:
        raise CommentError(
            f"byte range {span.start_byte}:{span.end_byte} is not a comment in this source"
        )
    if span.start_byte == 0 and span.text.startswith(b"#!"):
        raise CommentError("refusing to rewrite the shebang line")

    original = span.text
    text = bytes(text)
    if original.startswith(b"/*"):
        _check_block_rewrite(span, text)
    else:
        _check_line_rewrite(span, text)

    # Last, because the checks above give a more useful reason. This one catches
    # what they cannot see: text that ends the comment early and spills the rest
    # into the file as code.
    if not _parses_as_one_comment(text):
        raise CommentError(
            f"rewrite of {span.start_byte}:{span.end_byte} does not parse as one comment"
        )


def _check_block_rewrite(span: CommentSpan, text: bytes) -> None:
    if text.startswith(b"#"):
        raise CommentError(
            f"rewrite of {span.start_byte}:{span.end_byte} changes comment style "
            "from block to line"
        )
    if len(text) < 4 or not text.startswith(b"/*") or not text.endswith(b"*/"):
        raise CommentError(f"rewrite of {span.start_byte}:{span.end_byte} is not a comment")


def _check_line_rewrite(span: CommentSpan, text: bytes) -> None:
    original = span.text
    if text.startswith(b"/*"):
        raise CommentError(
            f"rewrite of {span.start_byte}:{span.end_byte} changes comment style "
            "from line to block"
        )
    if not text.startswith(b"#"):
        raise CommentError(f"rewrite of {span.start_byte}:{span.end_byte} is not a comment")
    if _marker_run(text) != _marker_run(original):
        raise CommentError(
            f"rewrite of {span.start_byte}:{span.end_byte} changes the comment marker "
            f"from {_marker_run(original) * b'#'!r} to {_marker_run(text) * b'#'!r}"
        )
    if b"\n" in text or b"\r" in text:
        raise CommentError(
            f"rewrite of {span.start_byte}:{span.end_byte} puts a newline inside a "
            "line comment"
        )


def comment_for(comments, span) -> CommentSpan | None:
    """Return the parsed comment matching ``span``'s position, if any."""
    for comment in comments:
        if comment.start_byte == span.start_byte and comment.end_byte == span.end_byte:
            return comment
    return None


def _parses_as_one_comment(text: bytes) -> bool:
    """True when ``text`` is a single comment covering the whole of ``text``.

    The delimiter checks above cannot see that `/* a */ */` ends the comment
    early and leaves the rest as code. Parsing the fragment can.
    """
    from tree_sitter import Parser

    from .comments import load_language

    root = Parser(load_language()).parse(text).root_node
    children = root.children
    return (
        len(children) == 1
        and children[0].type == "comment"
        and children[0].start_byte == 0
        and children[0].end_byte == len(text)
    )


def _marker_run(text: bytes) -> int:
    """How many ``#`` characters the comment opens with."""
    count = 0
    for byte in text:
        if byte != 0x23:  # '#'
            break
        count += 1
    return count
