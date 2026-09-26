"""Tests for the style policy the rewriting follows.

humanizer-ru is vendored, not restated: the ban table lives in the skill's own
file and is read from there. These tests hold two promises — that the table is
really read (fail-closed if it is missing), and that the guard only refuses what
the rewrite itself brought in.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from commahum.bans import hard_bans, introduced_bans, markers_path
from commahum.rewrite import INSTRUCTIONS


class VendoredRulesTests(unittest.TestCase):
    def test_the_table_is_read_from_the_vendored_skill(self) -> None:
        names = [name for name, _ in hard_bans()]

        self.assertGreaterEqual(len(names), 21)
        self.assertIn("Длинное тире", names)
        self.assertTrue(markers_path().is_file())
        self.assertEqual(markers_path().name, "markers.py")

    def test_a_missing_table_is_an_error_not_a_silent_pass(self) -> None:
        missing = Path("/nonexistent/markers.py")
        with patch("commahum.bans._MARKERS", missing):
            hard_bans.cache_clear()
            self.addCleanup(hard_bans.cache_clear)
            with self.assertRaises(RuntimeError) as caught:
                hard_bans()

        self.assertIn("missing", str(caught.exception))

    def test_the_patterns_match_the_constructs_they_name(self) -> None:
        patterns = dict(hard_bans())

        self.assertTrue(patterns["Длинное тире"].search("режим — быстрый"))
        self.assertTrue(patterns["Стоит отметить, что"].search("Стоит отметить, что всё иначе"))
        self.assertTrue(patterns["Данный/Данная/Данное"].search("данный файл"))
        self.assertFalse(patterns["Длинное тире"].search("режим, быстрый"))


class IntroducedBansTests(unittest.TestCase):
    def test_a_dash_the_rewrite_added_is_caught(self) -> None:
        added = introduced_bans("16 ГиБ ОЗУ, гибернация не объявлена", "ОЗУ — 16 ГиБ")

        self.assertEqual(added, ("Длинное тире",))

    def test_a_ban_already_in_the_original_is_not_the_rewrite_s_doing(self) -> None:
        self.assertEqual(introduced_bans("данный файл описывает", "данный файл описывает"), ())
        self.assertEqual(introduced_bans("данный файл", "данный файл, ничего больше"), ())

    def test_removing_a_ban_is_the_point_and_passes(self) -> None:
        self.assertEqual(introduced_bans("данный файл", "этот файл"), ())
        self.assertEqual(
            introduced_bans("Стоит отметить, что swap не нужен", "swap не нужен"), ()
        )

    def test_several_new_bans_are_all_named(self) -> None:
        added = introduced_bans("сборка идёт", "стоит отметить, что сборка — это комплексный подход")

        self.assertIn("Стоит отметить, что", added)
        self.assertIn("Длинное тире", added)
        self.assertIn("Комплексный подход/решение", added)


class InstructionsTests(unittest.TestCase):
    """The prompt is a promise to follow the vendored rules, so it is checked
    against them: every ban the skill lists is named in the instructions."""

    #: Vendored ban name -> the phrase the instructions must carry for it.
    WANTED = {
        "Не просто X, а Y": "не просто X, а Y",
        "Не только X, но и Y": "не только X, но и Y",
        "В современном мире": "в современном мире",
        "Стоит отметить, что": "стоит отметить",
        "Важно понимать/помнить/отметить, что": "важно понимать",
        "Данный/Данная/Данное": "данный/данная/данное",
        "Является": "«является»",
        "Играет важную/ключевую роль": "играет важную роль",
        "Можно с уверенностью сказать": "можно с уверенностью сказать",
        "Подводя итог / Таким образом": "подводя итог",
        "Длинное тире": "длинное тире",
        "Короткое тире": "короткое тире",
        "от X до Y (ложный диапазон)": "от X до Y",
        "В условиях [прил.] [сущ.]": "в условиях",
        "Погрузимся / посмотрим поближе": "погрузимся",
        "И вот здесь начинается самое интересное": "и вот здесь начинается",
        "Раскрыть потенциал": "раскрыть потенциал",
        "Вывести на новый уровень": "вывести на новый",
        "Комплексный подход/решение": "комплексный подход",
        "В связи с этим": "в связи с этим",
        "Открывает новые горизонты/перспективы/возможности": "открывает новые горизонты",
    }

    def test_the_vendored_table_is_exactly_what_the_instructions_cover(self) -> None:
        names = sorted(name for name, _ in hard_bans())

        self.assertEqual(
            names,
            sorted(self.WANTED),
            "the vendored HARD BANS changed; revisit INSTRUCTIONS and this table",
        )

    def test_every_ban_is_named_in_the_instructions(self) -> None:
        for name, phrase in self.WANTED.items():
            with self.subTest(ban=name):
                self.assertIn(phrase.lower(), INSTRUCTIONS.lower())

    def test_the_procedure_come_from_the_skill_not_from_this_project(self) -> None:
        skill = (markers_path().parents[2] / "SKILL.md").read_text(encoding="utf-8")

        for fragment in ("Жёсткие запреты", "Факт-замок"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, skill)
                self.assertIn(fragment.lower(), INSTRUCTIONS.lower())

        for rule in ("удаляй, не дописывай", "канцелярит", "факты"):
            with self.subTest(rule=rule):
                self.assertIn(rule, skill.lower())
                self.assertIn(rule, INSTRUCTIONS.lower())


if __name__ == "__main__":
    unittest.main()
