"""Tests for comment extraction.

The invariant these tests defend: a comment range covers a comment and nothing
else. `#` or `/* */` inside strings and interpolation is not a comment, and the
ranges are byte offsets so non-ASCII comments survive intact.
"""

from __future__ import annotations

import unittest

from commahum.comments import CommentSpan, find_comment_spans


def span_of(source: bytes, needle: bytes) -> tuple[int, int]:
    start = source.index(needle)
    return start, start + len(needle)


class FindCommentSpansTests(unittest.TestCase):
    def test_finds_line_and_block_comments_by_byte_range(self) -> None:
        source = b"# one\n{ x = 1; /* two */ }\n"

        spans = find_comment_spans(source)

        self.assertEqual(
            [(span.start_byte, span.end_byte) for span in spans],
            [span_of(source, b"# one"), span_of(source, b"/* two */")],
        )

    def test_does_not_report_comment_like_text_inside_strings(self) -> None:
        source = b'{ name = "# not a comment"; ok = 1; }\n'

        self.assertEqual(find_comment_spans(source), ())

    def test_does_not_report_block_comment_like_text_inside_strings(self) -> None:
        source = b'{ name = "/* not a comment */"; ok = 1; }\n'

        self.assertEqual(find_comment_spans(source), ())

    def test_ranges_are_byte_offsets_for_non_ascii_comments(self) -> None:
        source = "# комментарий\n{ x = 1; }\n".encode()

        (span,) = find_comment_spans(source)

        self.assertEqual(span.text, "# комментарий".encode())
        self.assertEqual((span.start_byte, span.end_byte), span_of(source, "# комментарий".encode()))

    def test_returns_adjacent_comments_in_source_order(self) -> None:
        source = b"# first\n# second\n/* third */\n{}\n"

        spans = find_comment_spans(source)

        self.assertEqual(
            [span.text for span in spans],
            [b"# first", b"# second", b"/* third */"],
        )

    def test_reports_multiline_block_comment_as_one_span(self) -> None:
        source = b"/* line one\n   line two */\n{}\n"

        (span,) = find_comment_spans(source)

        self.assertEqual(span.text, b"/* line one\n   line two */")

    def test_span_reports_its_own_source_text(self) -> None:
        source = b"{ x = 1; } # trailing\n"
        start, end = span_of(source, b"# trailing")

        span = CommentSpan(start, end, source)

        self.assertEqual(span.text, b"# trailing")
        self.assertEqual(span.start_byte, start)
        self.assertEqual(span.end_byte, end)

    def test_unparsable_source_still_yields_its_comments(self) -> None:
        # A half-written file is the normal state of an edit in progress; the
        # parser recovers and the comments are still rewritable.
        source = b"# leading\n{ x = ; }\n"

        spans = find_comment_spans(source)

        self.assertEqual([span.text for span in spans], [b"# leading"])


if __name__ == "__main__":
    unittest.main()
