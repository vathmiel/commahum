"""Tests for the deterministic technical-value guard.

The guard is narrow on purpose: it knows paths, links, hosts and digit-bearing
tokens (ports, counts, versions), and it refuses a rewrite that drops, changes
or invents one. It knows nothing about meaning, so the false-positive controls
matter as much as the refusals: ordinary Russian prose — hyphens, inflections,
slashes between words, reordered clauses — must pass untouched.
"""

from __future__ import annotations

import unittest

from commahum.values import technical_values, value_drift


class ExtractionTests(unittest.TestCase):
    def test_paths_are_values(self) -> None:
        values = technical_values(
            "конфиг лежит в /etc/nixos/configuration.nix, рядом ./flake.nix"
        )

        self.assertIn("/etc/nixos/configuration.nix", values)
        self.assertIn("./flake.nix", values)

    def test_bare_relative_file_path_is_one_value_even_with_hostlike_suffix(self) -> None:
        self.assertEqual(technical_values("services/api.dev"), ("services/api.dev",))

    def test_extensionless_relative_path_is_one_value(self) -> None:
        self.assertEqual(technical_values("vendor/pi"), ("vendor/pi",))

    def test_a_trailing_slash_is_not_a_different_path(self) -> None:
        self.assertEqual(technical_values("см. /etc/nixos/"), technical_values("см. /etc/nixos"))

    def test_a_url_is_a_value(self) -> None:
        url = "git+ssh://git@github.com/nix-community/nixos-config.git"

        self.assertIn(url, technical_values(f"репозиторий: {url}"))

    def test_a_url_that_ends_a_sentence_is_normalized(self) -> None:
        self.assertEqual(
            technical_values("см. https://nixos.org."),
            technical_values("см. https://nixos.org"),
        )

    def test_a_url_terminal_query_mark_is_part_of_the_value(self) -> None:
        self.assertEqual(technical_values("https://example.com/api?"), ("https://example.com/api?",))

    def test_a_host_a_port_and_localhost_are_values(self) -> None:
        values = technical_values("слушает на localhost:8384, доступен как nixos.org")

        self.assertIn("localhost", values)
        self.assertIn("nixos.org", values)
        self.assertIn("8384", values)

    def test_multisegment_host_path_is_one_value(self) -> None:
        self.assertEqual(technical_values("example.com/api/login"), ("example.com/api/login",))
        self.assertEqual(technical_values("example.com/api/@login"), ("example.com/api/@login",))

    def test_versions_counts_and_ip_addresses_are_values(self) -> None:
        values = technical_values("Python 3.11, nixpkgs 24.05, 16 ГиБ ОЗУ, bind 0.0.0.0")

        for expected in ("3.11", "24.05", "16", "0.0.0.0"):
            with self.subTest(value=expected):
                self.assertIn(expected, values)

    def test_a_hyphenated_compound_keeps_its_number_alone(self) -> None:
        self.assertEqual(technical_values("16-ядерный процессор"), ("16",))

    def test_ordinary_russian_prose_has_no_values(self) -> None:
        for prose in (
            "какой-то веб-сервер из-за границы упал",
            "включить и/или выключить",
            "работает в онлайн/оффлайн режиме",
            "во-первых, это быстро; во-вторых, дёшево",
            "т. е. примерно так",
            "он когда-то работал",
            "i.e. the same as before",
        ):
            with self.subTest(prose=prose):
                self.assertEqual(technical_values(prose), ())


class DriftTests(unittest.TestCase):
    def test_an_unchanged_body_never_drifts(self) -> None:
        body = "Сервер слушает порт 8384, конфиг в /etc/nixos"

        self.assertEqual(value_drift(body, body), ())

    def test_a_reordered_sentence_keeps_its_values(self) -> None:
        self.assertEqual(value_drift("Сервер слушает порт 8384", "Порт 8384 слушает сервер"), ())

    def test_a_changed_port_is_a_loss_and_an_invention(self) -> None:
        self.assertEqual(value_drift("порт 8384", "порт 8080"), ("lost 8384", "gained 8080"))

    def test_a_number_spelled_in_words_is_a_loss(self) -> None:
        self.assertEqual(value_drift("Ждать 5 минут", "Ждать пять минут"), ("lost 5",))

    def test_an_ordinal_spelled_in_words_is_a_loss(self) -> None:
        self.assertEqual(value_drift("3-й пункт", "третий пункт"), ("lost 3",))

    def test_a_lost_path_is_reported(self) -> None:
        self.assertEqual(
            value_drift("Конфиг в /etc/nixos", "Конфиг где-то"), ("lost /etc/nixos",)
        )

    def test_a_changed_path_is_a_loss_and_an_invention(self) -> None:
        self.assertEqual(
            value_drift("/etc/nixos", "/etc/nixos.bak"),
            ("lost /etc/nixos", "gained /etc/nixos.bak"),
        )

    def test_a_changed_bare_relative_nix_path_is_refused(self) -> None:
        self.assertEqual(
            value_drift("Конфиг в services/nginx.nix", "Конфиг в services/apache.nix"),
            ("lost services/nginx.nix", "gained services/apache.nix"),
        )

    def test_a_changed_extensionless_relative_path_is_refused(self) -> None:
        self.assertEqual(
            value_drift("Бандл в vendor/pi", "Бандл в vendor/node"),
            ("lost vendor/pi", "gained vendor/node"),
        )

    def test_path_case_is_not_normalized_away(self) -> None:
        self.assertEqual(
            value_drift("в /etc/NixOS", "в /etc/nixos"),
            ("lost /etc/NixOS", "gained /etc/nixos"),
        )

    def test_an_invented_url_is_reported(self) -> None:
        self.assertEqual(
            value_drift("Скачать с сайта", "Скачать с https://nixos.org"),
            ("gained https://nixos.org",),
        )

    def test_a_changed_version_is_a_loss_and_an_invention(self) -> None:
        self.assertEqual(
            value_drift("Nix 2.24.9", "Nix 2.24.10"), ("lost 2.24.9", "gained 2.24.10")
        )

    def test_a_url_is_reported_once_even_with_numbers_inside(self) -> None:
        self.assertEqual(
            value_drift("см. https://example.com/v2/api", "см. https://example.com/v3/api"),
            ("lost https://example.com/v2/api", "gained https://example.com/v3/api"),
        )

    def test_a_url_trailing_slash_is_significant(self) -> None:
        self.assertEqual(
            value_drift("см. https://example.com/api/", "см. https://example.com/api"),
            ("lost https://example.com/api/", "gained https://example.com/api"),
        )

    def test_a_url_terminal_query_mark_is_significant(self) -> None:
        self.assertEqual(
            value_drift("см. https://example.com/api?", "см. https://example.com/api"),
            ("lost https://example.com/api?", "gained https://example.com/api"),
        )

    def test_a_changed_multisegment_host_path_is_refused(self) -> None:
        self.assertEqual(
            value_drift("узел example.com/api/login", "узел example.com/api/admin"),
            ("lost example.com/api/login", "gained example.com/api/admin"),
        )
        self.assertEqual(
            value_drift("узел example.com/api/@login", "узел example.com/api/@admin"),
            ("lost example.com/api/@login", "gained example.com/api/@admin"),
        )

    def test_losing_one_of_two_identical_numbers_is_a_loss(self) -> None:
        self.assertEqual(value_drift("2 ядра, 2 модуля", "2 ядра и модуль"), ("lost 2",))

    def test_a_range_written_out_in_words_keeps_both_numbers(self) -> None:
        self.assertEqual(value_drift("Занято 10-15 минут", "Занято от 10 до 15 минут"), ())

    def test_cyrillic_prose_without_values_never_drifts(self) -> None:
        cases = (
            ("Веб-сервер падает из-за нехватки памяти", "Сервер падает: памяти не хватает"),
            ("Включить и/или выключить", "Включить или выключить"),
            ("Переключение онлайн/оффлайн", "Переключение между режимами"),
            ("Какой-то веб-сервер из-за границы", "Некий сервер из-за границы упал"),
        )
        for before, after in cases:
            with self.subTest(before=before):
                self.assertEqual(value_drift(before, after), ())

    def test_bytes_that_are_not_utf8_do_not_break_the_guard(self) -> None:
        """Comment bytes reach the guard through `surrogateescape`, like everywhere else."""
        body = b"\xff\xfe \xd0\xbf\xd0\xbe\xd1\x80\xd1\x82 8080".decode("utf-8", "surrogateescape")

        self.assertEqual(value_drift(body, body), ())
        self.assertIn("8080", technical_values(body))


if __name__ == "__main__":
    unittest.main()
