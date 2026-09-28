"""A saved Commahum model does not alter pi's global preferences."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from commahum import asking
from commahum.cli import main
from commahum.skill import vendored_skill


class ModelPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    def command(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(["model", *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def selected(self, model=None, provider=None):
        options = SimpleNamespace(model=model, provider=provider, catalog=False)
        with patch("commahum.asking.pi_generator") as generator:
            asking.generator(options, skill=vendored_skill())
            return generator.call_args.args[:2]

    def test_saved_model_is_used_by_next_model_call(self) -> None:
        self.assertEqual(self.command("set", "deepseek/deepseek-flash")[0], 0)
        self.assertEqual(self.selected(), ("deepseek/deepseek-flash", None))
        self.assertIn("deepseek/deepseek-flash", self.command()[1])

    def test_explicit_model_overrides_saved_model(self) -> None:
        self.command("set", "deepseek/deepseek-flash")
        self.assertEqual(self.selected("openai-codex/gpt-5.5"), ("openai-codex/gpt-5.5", None))

    def test_short_model_with_explicit_provider_is_still_supported(self) -> None:
        self.command("set", "deepseek/deepseek-flash")
        self.assertEqual(self.selected("claude-sonnet", "anthropic"), ("claude-sonnet", "anthropic"))

    def test_clear_returns_to_pis_default(self) -> None:
        self.command("set", "deepseek/deepseek-flash")
        self.assertEqual(self.command("clear")[0], 0)
        self.assertEqual(self.selected(), (None, None))

    def test_invalid_model_is_not_saved(self) -> None:
        status, _, error = self.command("set", "deepseek-flash")
        self.assertEqual(status, 2)
        self.assertIn("provider/id", error)
        self.assertEqual(self.selected(), (None, None))

    def test_control_characters_cannot_be_saved_or_printed(self) -> None:
        for value in ("deepseek/flash\x00", "deepseek/flash\x1b[31m"):
            with self.subTest(value=value):
                status, out, _ = self.command("set", value)
                self.assertEqual(status, 2)
                self.assertEqual(out, "")
        config = Path(self.directory.name) / "commahum" / "config.json"
        config.parent.mkdir(exist_ok=True)
        config.write_text('{"model":"deepseek/flash\\u001b[31m"}')
        status, out, error = self.command()
        self.assertEqual(status, 2)
        self.assertEqual(out, "")
        self.assertNotIn("\x1b", error)

    def test_filesystem_errors_are_reported_without_traceback(self) -> None:
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/dev/null"}):
            for action in (("set", "deepseek/deepseek-flash"), ("clear",)):
                with self.subTest(action=action):
                    status, _, error = self.command(*action)
                    self.assertEqual(status, 2)
                    self.assertIn("commahum:", error)
                    self.assertNotIn("Traceback", error)

    def test_config_path_with_control_characters_is_escaped_in_errors(self) -> None:
        bad_path = Path(self.directory.name) / "config-\x1b[2J"
        bad_path.write_text("")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(bad_path)}):
            for action in ((), ("set", "deepseek/deepseek-flash"), ("clear",)):
                with self.subTest(action=action):
                    status, _, error = self.command(*action)
                    self.assertEqual(status, 2)
                    self.assertNotIn("\x1b", error)
                    self.assertIn("\\x1b", error)

    def test_extra_empty_argument_is_rejected(self) -> None:
        for action in ("show", "clear"):
            with self.subTest(action=action):
                status, _, error = self.command(action, "")
                self.assertEqual(status, 2)
                self.assertIn("takes no value", error)

    def test_corrupt_saved_preference_refuses_to_call_wrong_provider(self) -> None:
        self.command("set", "deepseek/deepseek-flash")
        config = Path(self.directory.name) / "commahum" / "config.json"
        config.parent.mkdir(exist_ok=True)
        config.write_text("not json")
        with self.assertRaisesRegex(RuntimeError, "config.json"):
            self.selected()

    def test_conflicting_provider_does_not_silently_choose_another_one(self) -> None:
        self.command("set", "deepseek/deepseek-flash")
        with self.assertRaisesRegex(RuntimeError, "--model"):
            self.selected(provider="anthropic")

    def test_conflicting_provider_error_does_not_print_control_characters(self) -> None:
        self.command("set", "deepseek/deepseek-flash")
        with self.assertRaises(RuntimeError) as raised:
            self.selected(provider="anthropic\x1b[2J")
        self.assertNotIn("\x1b", str(raised.exception))
        self.assertIn("\\x1b", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
