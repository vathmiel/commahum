"""Tests for the rewriting round trip.

The model is a collaborator, so it is faked here: what is under test is the
contract around it — the model sees prose and nothing else, its answer is
matched back by number, and anything that does not survive validation costs one
retry and then gets dropped, never forced in.
"""

from __future__ import annotations

import unittest

from commahum.comments import find_comment_spans
from commahum.rewrite import RewriteResult, build_request, parse_reply, rewrite_source


class FakeGenerator:
    """A stand-in model: returns canned replies and records the prompts."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("the generator was called more often than expected")
        return self.replies.pop(0)


def code_segments(source: bytes) -> list[bytes]:
    segments: list[bytes] = []
    position = 0
    for span in find_comment_spans(source):
        segments.append(source[position : span.start_byte])
        position = span.end_byte
    segments.append(source[position:])
    return segments


class BuildRequestTests(unittest.TestCase):
    def test_numbers_the_bodies_in_order(self) -> None:
        request = build_request([(1, "первый".encode()), (2, "второй".encode())])

        self.assertIn("[[1]]\nпервый", request)
        self.assertIn("[[2]]\nвторой", request)

    def test_the_skill_opening_comes_first_and_on_the_same_line(self) -> None:
        """pi reads the skill name up to the first space, so the space is load-bearing."""
        request = build_request([(1, "первый".encode())], invocation="/skill:humanizer-ru")

        self.assertTrue(request.startswith("/skill:humanizer-ru Комментарии:"), request[:60])
        self.assertNotIn("/skill:humanizer-ru\n", request)

    def test_without_an_invocation_the_payload_is_just_the_payload(self) -> None:
        request = build_request([(1, "первый".encode())], invocation=None)

        self.assertTrue(request.startswith("Комментарии:"))
        self.assertNotIn("/skill:", request)

    def test_never_includes_code_or_comment_markers(self) -> None:
        source = (
            "# TODO: починить сборку\n"
            "{ secretName = \"private\"; /* вторая часть */ }\n"
        ).encode()
        from commahum.selection import select_candidates

        candidates = select_candidates(source).candidates
        request = build_request([(index, candidate.body) for index, candidate in enumerate(candidates, 1)])

        self.assertNotIn("secretName", request)
        self.assertNotIn("TODO", request)
        self.assertNotIn("#", request)
        self.assertIn("починить сборку", request)
        self.assertIn("вторая часть", request)


class ParseReplyTests(unittest.TestCase):
    def test_reads_bodies_back_by_number(self) -> None:
        reply = "[[1]]\nпервый\n[[2]]\nвторой\n"

        bodies = parse_reply(reply, [1, 2])

        self.assertEqual(bodies, {1: "первый".encode(), 2: "второй".encode()})

    def test_ignores_chatter_around_the_sections(self) -> None:
        reply = "Готово:\n[[1]]\nпервый\nСпасибо!\n"

        bodies = parse_reply(reply, [1])

        self.assertEqual(bodies, {1: "первый".encode()})

    def test_unwraps_a_code_fence(self) -> None:
        reply = "[[1]]\n```\nпервый\n```\n"

        bodies = parse_reply(reply, [1])

        self.assertEqual(bodies, {1: "первый".encode()})

    def test_keeps_a_multiline_body_for_a_block_comment(self) -> None:
        reply = "[[1]]\nстрока один\nстрока два\n"

        bodies = parse_reply(reply, [1], multiline={1})

        self.assertEqual(bodies, {1: "строка один\nстрока два".encode()})

    def test_reads_one_line_for_a_line_comment_and_ignores_the_rest(self) -> None:
        reply = "[[1]]\nпервый\nи ещё что-то\n"

        bodies = parse_reply(reply, [1])

        self.assertEqual(bodies, {1: "первый".encode()})

    def test_reports_numbers_the_model_left_out(self) -> None:
        reply = "[[1]]\nпервый\n"

        bodies = parse_reply(reply, [1, 2])

        self.assertEqual(bodies, {1: "первый".encode()})

    def test_ignores_numbers_that_were_not_asked_for(self) -> None:
        reply = "[[1]]\nпервый\n[[9]]\nлишний\n"

        bodies = parse_reply(reply, [1])

        self.assertEqual(bodies, {1: "первый".encode()})


class RewriteSourceTests(unittest.TestCase):
    def test_rewrites_every_comment_and_touches_no_code(self) -> None:
        source = "# первый\n{ x = 1; }\n/* второй */\n".encode()
        generator = FakeGenerator("[[1]]\nПЕРВЫЙ\n[[2]]\nВТОРОЙ\n")

        result = rewrite_source(source, generator)

        self.assertEqual(result.rewritten, "# ПЕРВЫЙ\n{ x = 1; }\n/* ВТОРОЙ */\n".encode())
        self.assertEqual(code_segments(source), code_segments(result.rewritten))
        self.assertEqual(len(result.applied), 2)
        self.assertEqual(result.unrewritten, ())

    def test_keeps_the_original_when_the_reply_never_holds_up(self) -> None:
        source = "# первый\n{}\n".encode()
        generator = FakeGenerator("", "")

        result = rewrite_source(source, generator)

        self.assertEqual(result.rewritten, source)
        self.assertEqual(result.applied, ())
        self.assertEqual(len(result.unrewritten), 1)
        self.assertEqual(result.requests, 2)

    def test_retries_only_the_comments_that_failed(self) -> None:
        source = "# первый\n{}\n# второй\n".encode()
        generator = FakeGenerator(
            "[[1]]\nпервый\n",  # 2 is missing, 1 is fine
            "[[2]]\nВТОРОЙ\n",
        )

        result = rewrite_source(source, generator)

        self.assertEqual(result.rewritten, "# первый\n{}\n# ВТОРОЙ\n".encode())
        self.assertEqual(result.applied, (1, 2))
        self.assertEqual(result.requests, 2)
        self.assertIn("[[2]]", generator.prompts[1])
        self.assertNotIn("[[1]]", generator.prompts[1])

    def test_rejects_a_body_that_would_break_the_comment_and_retries_it(self) -> None:
        source = "/* первый */\n{}\n".encode()
        generator = FakeGenerator(
            "[[1]]\nвторой */\n",  # would close the comment early
            "[[1]]\nвторой\n",
        )

        result = rewrite_source(source, generator)

        self.assertEqual(result.rewritten, "/* второй */\n{}\n".encode())
        self.assertEqual(result.applied, (1,))
        self.assertEqual(result.requests, 2)

    def test_does_not_ask_the_model_when_there_is_nothing_to_rewrite(self) -> None:
        source = "# nix-shell -p hello\n{}\n".encode()
        generator = FakeGenerator()

        result = rewrite_source(source, generator)

        self.assertEqual(result.rewritten, source)
        self.assertEqual(generator.prompts, [])
        self.assertEqual(result.requests, 0)

    def test_reports_which_comments_it_left_alone(self) -> None:
        source = "# текст\n# nix-shell -p hello\n{}\n".encode()
        generator = FakeGenerator("[[1]]\nНОВЫЙ\n")

        result = rewrite_source(source, generator)

        self.assertEqual([reason for _, reason in result.skipped], ["directive"])

    def test_returns_a_result_object(self) -> None:
        result = rewrite_source(b"{}\n", FakeGenerator())

        self.assertIsInstance(result, RewriteResult)

    def test_refuses_a_rewrite_that_brings_in_a_hard_ban_and_retries_it(self) -> None:
        source = "# 16 ГиБ ОЗУ, гибернация не объявлена\n{}\n".encode()
        generator = FakeGenerator(
            "[[1]]\nОЗУ — 16 ГиБ\n",  # the dash the original never had
            "[[1]]\nОЗУ 16 ГиБ\n",
        )

        result = rewrite_source(source, generator)

        self.assertEqual(result.rewritten, "# ОЗУ 16 ГиБ\n{}\n".encode())
        self.assertEqual(result.applied, (1,))
        self.assertEqual(result.requests, 2)

    def test_keeps_the_original_when_the_ban_comes_back_again(self) -> None:
        source = "# сборка идёт\n{}\n".encode()
        generator = FakeGenerator(
            "[[1]]\nстоит отметить, что сборка идёт\n",
            "[[1]]\nважно отметить: сборка идёт\n",
        )

        result = rewrite_source(source, generator)

        self.assertEqual(result.rewritten, source)
        self.assertEqual(
            result.unrewritten,
            ((1, "adds a hard ban: Важно понимать/помнить/отметить, что"),),
        )

    def test_a_ban_the_original_already_had_is_left_to_the_model(self) -> None:
        source = "# данный файл описывает swap\n{}\n".encode()
        generator = FakeGenerator("[[1]]\nданный файл описывает swap\n")

        result = rewrite_source(source, generator)

        self.assertEqual(result.applied, (1,))
        self.assertEqual(result.unrewritten, ())


if __name__ == "__main__":
    unittest.main()
