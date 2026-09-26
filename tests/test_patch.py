"""Tests for the byte-range splice.

The patcher is where the guarantee lives: it may change comment bytes and
nothing else. Every rule here exists because breaking it would let a rewrite
land outside a comment or quietly destroy code.
"""

from __future__ import annotations

import unittest

from commahum.comments import CommentError, find_comment_spans
from commahum.patch import Rewrite, apply_rewrites


def code_segments(source: bytes) -> list[bytes]:
    """Every non-comment byte run in ``source``, in order."""
    segments: list[bytes] = []
    position = 0
    for span in find_comment_spans(source):
        segments.append(source[position : span.start_byte])
        position = span.end_byte
    segments.append(source[position:])
    return segments


class ApplyRewritesTests(unittest.TestCase):
    def test_replaces_only_the_comment_bytes(self) -> None:
        source = "# старое\n{ x = 1; }\n".encode()
        (span,) = find_comment_spans(source)

        result = apply_rewrites(source, [Rewrite(span, "# новое".encode())])

        self.assertEqual(result, "# новое\n{ x = 1; }\n".encode())
        self.assertEqual(code_segments(source), code_segments(result))

    def test_leaves_code_untouched_across_many_rewrites(self) -> None:
        source = (
            "# первый\n"
            "{\n"
            '  name = "значение"; # второй\n'
            "  /* третий */\n"
            "  nested = { deep = \"# не комментарий\"; };\n"
            "}\n"
        ).encode()
        spans = find_comment_spans(source)
        self.assertEqual(len(spans), 3)

        rewrites = [
            Rewrite(spans[0], "# первый переписанный".encode()),
            Rewrite(spans[1], "# второй переписанный".encode()),
            Rewrite(spans[2], "/* третий переписанный */".encode()),
        ]
        result = apply_rewrites(source, rewrites)

        self.assertIn("первый переписанный".encode(), result)
        self.assertIn("второй переписанный".encode(), result)
        self.assertIn("третий переписанный".encode(), result)
        self.assertEqual(code_segments(source), code_segments(result))
        self.assertEqual(len(find_comment_spans(result)), len(spans))

    def test_returns_the_source_unchanged_without_rewrites(self) -> None:
        source = "# комментарий\n{}\n".encode()

        self.assertEqual(apply_rewrites(source, []), source)

    def test_ignores_rewrite_order(self) -> None:
        source = "# один\n{}\n# два\n".encode()
        first, second = find_comment_spans(source)

        forwards = apply_rewrites(
            source, [Rewrite(first, "# раз".encode()), Rewrite(second, "# дважды".encode())]
        )
        backwards = apply_rewrites(
            source, [Rewrite(second, "# дважды".encode()), Rewrite(first, "# раз".encode())]
        )

        self.assertEqual(forwards, backwards)

    def test_refuses_a_range_that_is_not_a_comment(self) -> None:
        source = "# комментарий\n{ x = 1; }\n".encode()
        code_start = source.index(b"{ x")
        alibi = type(find_comment_spans(source)[0])(code_start, code_start + 5, source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(alibi, b"x = 2")])

        self.assertIn("not a comment", str(caught.exception))

    def test_refuses_overlapping_rewrites(self) -> None:
        source = "# один\n{}\n".encode()
        (span,) = find_comment_spans(source)
        inside = type(span)(span.start_byte, span.end_byte - 2, source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(
                source,
                [
                    Rewrite(span, "# раз".encode()),
                    Rewrite(inside, "# два".encode()),
                ],
            )

        self.assertIn("overlap", str(caught.exception))

    def test_refuses_a_span_from_a_different_source(self) -> None:
        original = "# комментарий\n{}\n".encode()
        other = "# комментарий\n{ y = 2; }\n".encode()
        (span,) = find_comment_spans(original)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(other, [Rewrite(span, "# новое".encode())])

        self.assertIn("different source", str(caught.exception))

    def test_refuses_rewrite_that_drops_the_comment_marker(self) -> None:
        source = "# комментарий\n{}\n".encode()
        (span,) = find_comment_spans(source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(span, "просто текст".encode())])

        self.assertIn("not a comment", str(caught.exception))

    def test_refuses_rewrite_that_changes_comment_style(self) -> None:
        source = "# комментарий\n{}\n".encode()
        (span,) = find_comment_spans(source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(span, "/* комментарий */".encode())])

        self.assertIn("style", str(caught.exception))

    def test_refuses_rewrite_that_drops_a_repeated_comment_marker(self) -> None:
        source = "## заголовок\n{}\n".encode()
        (span,) = find_comment_spans(source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(span, "# заголовок".encode())])

        self.assertIn("marker", str(caught.exception))

    def test_refuses_newline_inside_a_line_comment(self) -> None:
        source = "# комментарий\n{}\n".encode()
        (span,) = find_comment_spans(source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(span, "# первая\n# вторая".encode())])

        self.assertIn("newline", str(caught.exception))

    def test_allows_newline_inside_a_block_comment(self) -> None:
        source = "/* старый\n   текст */\n{}\n".encode()
        (span,) = find_comment_spans(source)

        result = apply_rewrites(source, [Rewrite(span, "/* новый\n   текст */".encode())])

        self.assertEqual(result, "/* новый\n   текст */\n{}\n".encode())

    def test_refuses_rewriting_a_shebang(self) -> None:
        source = "#!/usr/bin/env nix-shell\n# комментарий\n{}\n".encode()
        spans = find_comment_spans(source)
        self.assertEqual(len(spans), 2)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(spans[0], b"#!/bin/sh")])

        self.assertIn("shebang", str(caught.exception))

    def test_refuses_an_unterminated_block_comment(self) -> None:
        source = "/* старый */\n{}\n".encode()
        (span,) = find_comment_spans(source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(span, "/* новый".encode())])

        self.assertIn("not a comment", str(caught.exception))

    def test_refuses_text_that_closes_the_comment_early(self) -> None:
        source = "/* старый */\n{}\n".encode()
        (span,) = find_comment_spans(source)

        with self.assertRaises(CommentError) as caught:
            apply_rewrites(source, [Rewrite(span, "/* новый */ */".encode())])

        self.assertIn("one comment", str(caught.exception))

    def test_accepts_comment_markers_inside_the_text(self) -> None:
        # `/*` inside a line comment is text, not a comment: it must not be
        # mistaken for a second comment by the well-formedness check.
        source = "# старый\n{}\n".encode()
        (span,) = find_comment_spans(source)

        result = apply_rewrites(source, [Rewrite(span, "# пример /* не комментарий */".encode())])

        self.assertEqual(result, "# пример /* не комментарий */\n{}\n".encode())


if __name__ == "__main__":
    unittest.main()
