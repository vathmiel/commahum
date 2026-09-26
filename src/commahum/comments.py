"""Comment extraction for Nix sources.

The project rewrites comment text only. Everything that is not a comment must
survive byte-for-byte, so the parser hands back explicit byte ranges and the
patcher refuses to touch anything that is not one of those ranges.

Tree-sitter parses bytes, and node offsets are byte offsets, so a comment
containing non-ASCII text (which is the normal case here, since the first
version targets Russian) yields a range that still lines up with the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

__all__ = ["CommentSpan", "CommentError", "find_comment_spans", "load_language"]


class CommentError(RuntimeError):
    """Raised when a source file cannot be parsed or a range is not a comment."""


@lru_cache(maxsize=1)
def load_language():
    """Return the Tree-sitter Nix language object (loaded once per process)."""
    from tree_sitter import Language

    import tree_sitter_nix

    return Language(tree_sitter_nix.language())


@dataclass(frozen=True)
class CommentSpan:
    """A half-open byte range ``[start_byte, end_byte)`` holding one comment.

    ``source`` is kept so a span can describe itself; it is excluded from
    comparison and hashing, so spans compare by position, not by file.
    """

    start_byte: int
    end_byte: int
    source: bytes = field(compare=False, repr=False)

    @property
    def text(self) -> bytes:
        """The exact comment bytes, delimiters included."""
        return self.source[self.start_byte : self.end_byte]

    @property
    def is_block(self) -> bool:
        """True for ``/* ... */`` comments, false for ``# ...`` comments."""
        return self.text.startswith(b"/*")


def find_comment_spans(source: bytes) -> tuple[CommentSpan, ...]:
    """Return every comment in ``source`` as a ``CommentSpan``, in source order.

    Text that merely looks like a comment — inside a string or an interpolation
    — is not returned, because the parser knows the difference and a regex does
    not.
    """
    from tree_sitter import Parser

    if not isinstance(source, (bytes, bytearray, memoryview)):
        raise TypeError(f"source must be bytes, not {type(source).__name__}")
    source = bytes(source)

    tree = Parser(load_language()).parse(source)

    spans: list[CommentSpan] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "comment":
            spans.append(CommentSpan(node.start_byte, node.end_byte, source))
            continue
        # Reversed so the document order survives the LIFO stack. A comment
        # never contains another comment, so its subtree is skipped.
        stack.extend(reversed(node.children))
    spans.sort(key=lambda span: span.start_byte)
    return tuple(spans)
