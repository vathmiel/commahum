"""Tests for diff rendering.

The diff is the deliverable a human reads, so it has to be honest: it shows the
old and the new comment, keeps untouched code as context, and stays lossless
even when the file is not valid UTF-8.
"""

from __future__ import annotations

import unittest

from commahum.comments import find_comment_spans
from commahum.diff import render_diff
from commahum.patch import Rewrite, apply_rewrites


def rewrite_all(source: bytes, replacements: list[bytes]) -> bytes:
    spans = find_comment_spans(source)
    if len(spans) != len(replacements):
        raise AssertionError(f"expected {len(replacements)} comments, found {len(spans)}")
    return apply_rewrites(source, [Rewrite(span, text) for span, text in zip(spans, replacements)])


def changed_lines(diff: str) -> tuple[list[str], list[str]]:
    """Removed and added lines, with the ``---``/``+++`` headers left out."""
    removed = [line for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    added = [line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    return removed, added


class RenderDiffTests(unittest.TestCase):
    def test_shows_the_old_and_the_new_comment(self) -> None:
        original = "# старое\n{ x = 1; }\n".encode()
        rewritten = rewrite_all(original, ["# новое".encode()])

        diff = render_diff(original, rewritten, "example.nix")

        self.assertIn("-# старое\n", diff)
        self.assertIn("+# новое\n", diff)

    def test_keeps_untouched_code_as_context(self) -> None:
        original = "# старое\n{ x = 1; }\n".encode()
        rewritten = rewrite_all(original, ["# новое".encode()])

        diff = render_diff(original, rewritten, "example.nix")

        self.assertIn(" { x = 1; }\n", diff)

    def test_names_both_versions_of_the_file(self) -> None:
        original = "# старое\n{}\n".encode()
        rewritten = rewrite_all(original, ["# новое".encode()])

        diff = render_diff(original, rewritten, "modules/host.nix")

        self.assertIn("a/modules/host.nix", diff)
        self.assertIn("b/modules/host.nix", diff)

    def test_reports_no_diff_when_nothing_changed(self) -> None:
        source = "# комментарий\n{}\n".encode()

        self.assertEqual(render_diff(source, source, "example.nix"), "")

    def test_changed_lines_differ_only_in_their_comment_text(self) -> None:
        original = '{ name = "значение"; # старое\n  ok = true; }\n'.encode()
        rewritten = rewrite_all(original, ["# новое".encode()])

        diff = render_diff(original, rewritten, "example.nix")

        removed, added = changed_lines(diff)
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(added), 1)
        self.assertEqual(removed[0][1:].split("#")[0], added[0][1:].split("#")[0])

    def test_survives_bytes_that_are_not_valid_utf8(self) -> None:
        original = b'# \xff\xfe\n{ name = "\xc3("; }\n'
        rewritten = rewrite_all(original, ["# новое".encode()])

        diff = render_diff(original, rewritten, "example.nix")

        restored = b"".join(
            line.encode("utf-8", "surrogateescape") for line in diff.splitlines(keepends=True)
        )
        self.assertIn(b"# \xff\xfe\n", restored)
        self.assertIn(b'name = "\xc3("', restored)

    def test_one_removed_and_one_added_line_per_rewritten_comment(self) -> None:
        original = "# один\n{}\n# два\n".encode()
        rewritten = rewrite_all(original, ["# раз".encode(), "# дважды".encode()])

        diff = render_diff(original, rewritten, "example.nix")

        removed, added = changed_lines(diff)
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(added), 2)


if __name__ == "__main__":
    unittest.main()
