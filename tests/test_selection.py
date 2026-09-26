"""Tests for comment selection.

Selection decides what a model is even allowed to see. Two things are being
protected here: comments that are not prose (directives, bare URLs and paths,
separator lines) are never sent, and machine-readable prefixes — a `#` run, a
`TODO:` marker, comment delimiters — are split off before the model runs, so
they cannot be lost by a model that never received them.
"""

from __future__ import annotations

import unittest

from commahum.selection import Candidate, Selection, select_candidates


def only_candidate(source: bytes) -> Candidate:
    selection = select_candidates(source)
    if len(selection.candidates) != 1:
        raise AssertionError(f"expected 1 candidate, got {selection!r}")
    return selection.candidates[0]


class CandidateShapeTests(unittest.TestCase):
    def test_splits_a_plain_line_comment_into_prefix_and_body(self) -> None:
        candidate = only_candidate("# текст\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"# ")
        self.assertEqual(candidate.body, "текст".encode())
        self.assertEqual(candidate.suffix, b"")

    def test_keeps_a_repeated_marker_in_the_prefix(self) -> None:
        candidate = only_candidate("## заголовок\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"## ")
        self.assertEqual(candidate.body, "заголовок".encode())

    def test_handles_a_line_comment_without_a_space(self) -> None:
        candidate = only_candidate("#текст\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"#")
        self.assertEqual(candidate.body, "текст".encode())

    def test_splits_a_block_comment_into_prefix_body_and_suffix(self) -> None:
        candidate = only_candidate("/* текст */\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"/* ")
        self.assertEqual(candidate.body, "текст".encode())
        self.assertEqual(candidate.suffix, b" */")

    def test_handles_a_compact_block_comment(self) -> None:
        candidate = only_candidate("/*текст*/\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"/*")
        self.assertEqual(candidate.body, "текст".encode())
        self.assertEqual(candidate.suffix, b"*/")

    def test_assembles_a_new_comment_from_the_preserved_parts(self) -> None:
        candidate = only_candidate("# текст\n{}\n".encode())

        self.assertEqual(candidate.assemble("новый".encode()), "# новый".encode())

    def test_assembles_a_block_comment_from_the_preserved_parts(self) -> None:
        candidate = only_candidate("/* текст */\n{}\n".encode())

        self.assertEqual(candidate.assemble("новый".encode()), "/* новый */".encode())


class SelectCandidatesTests(unittest.TestCase):
    def test_keeps_candidates_in_source_order(self) -> None:
        source = "# первый\n{}\n/* второй */\n".encode()

        selection = select_candidates(source)

        self.assertEqual(
            [candidate.body for candidate in selection.candidates],
            ["первый".encode(), "второй".encode()],
        )

    def test_skips_the_shebang(self) -> None:
        source = "#!/usr/bin/env nix-shell\n# текст\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual([candidate.body for candidate in selection.candidates], ["текст".encode()])
        self.assertEqual([reason for _, reason in _reasons(selection)], ["shebang"])

    def test_skips_shell_directives(self) -> None:
        source = "# nix-shell -p hello\n# shellcheck disable=SC2034\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["directive", "directive"])

    def test_skips_editor_modelines(self) -> None:
        source = "# vim: set ft=nix:\n# -*- mode: nix -*-\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["directive", "directive"])

    def test_skips_machine_readable_license_tags(self) -> None:
        source = "# SPDX-License-Identifier: MIT\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["directive"])

    def test_skips_a_comment_that_is_only_a_url(self) -> None:
        source = "# https://example.com/a/b?c=d\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["not prose"])

    def test_skips_a_comment_that_is_only_paths(self) -> None:
        source = "# /etc/fonts ~/.config/fontconfig/ ./modules/home.nix\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["not prose"])

    def test_keeps_a_comment_that_mentions_paths_in_prose(self) -> None:
        source = "# Системный /etc/fonts уже настроен.\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual(len(selection.candidates), 1)

    def test_skips_a_separator_line(self) -> None:
        source = "# ----------------\n{}\n# ---- 2024 ----\n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["not prose", "not prose"])

    def test_skips_an_empty_comment(self) -> None:
        source = "#\n{}\n# \n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["empty", "empty"])

    def test_preserves_a_todo_marker_and_offers_only_its_body(self) -> None:
        candidate = only_candidate("# TODO: починить сборку\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"# TODO: ")
        self.assertEqual(candidate.body, "починить сборку".encode())

    def test_preserves_a_marker_without_a_colon(self) -> None:
        candidate = only_candidate("# XXX починить позже\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"# XXX ")
        self.assertEqual(candidate.body, "починить позже".encode())

    def test_skips_a_bare_marker_with_nothing_to_rewrite(self) -> None:
        source = "# TODO:\n{}\n".encode()

        selection = select_candidates(source)

        self.assertEqual(selection.candidates, ())
        self.assertEqual([reason for _, reason in _reasons(selection)], ["empty"])

    def test_does_not_treat_note_as_a_marker(self) -> None:
        candidate = only_candidate("# NOTE: это просто проза\n{}\n".encode())

        self.assertEqual(candidate.prefix, b"# ")
        self.assertEqual(candidate.body, "NOTE: это просто проза".encode())

    def test_reports_every_comment_as_either_a_candidate_or_a_skip(self) -> None:
        source = "# текст\n# nix-shell -p hello\n/* ещё */\n".encode()

        selection = select_candidates(source)

        self.assertEqual(len(selection.candidates) + len(selection.skipped), 3)

    def test_reassembling_an_untouched_body_returns_the_original_comment(self) -> None:
        source = (
            "#!/usr/bin/env nix-shell\n"
            "# первый\n"
            "## заголовок\n"
            "#TODO дописать\n"
            "/* второй */\n"
            "/*третий*/\n"
            "{}\n"
        ).encode()

        selection = select_candidates(source)

        self.assertEqual(len(selection.skipped), 1)
        for candidate in selection.candidates:
            self.assertEqual(candidate.assemble(candidate.body), candidate.span.text)


def _reasons(selection: Selection) -> list[tuple[int, str]]:
    return [(span.start_byte, reason) for span, reason in selection.skipped]


if __name__ == "__main__":
    unittest.main()
