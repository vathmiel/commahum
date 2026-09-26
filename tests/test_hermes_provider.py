"""The provider generator: the vendor layer's client, shaped for the rewriter.

No network here — the client is injected. What is checked is the contract the
rewriter depends on: the system prompt is the rewriter's instructions, the prompt
is the numbered comment payload, and a provider that cannot name a model is
refused rather than asked with an empty one.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from commahum.hermes_provider import (
    DEFAULT_TIMEOUT,
    load_credential_environment,
    provider_generator,
)
from commahum.rewrite import INSTRUCTIONS


class FakeClient:
    def __init__(self, text: str = "[[1]]\nпереписано\n") -> None:
        self.calls: list[dict] = []
        completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=completions)
        self._text = text

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self._text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def resolver(client, model):
    def resolve(provider, requested):
        return client, (requested or model)

    return resolve


class ProviderGeneratorTests(unittest.TestCase):
    def test_the_request_carries_the_rewriter_instructions_and_the_payload(self) -> None:
        client = FakeClient()
        generate = provider_generator("deepseek", resolve=resolver(client, "deepseek-flash"))

        answer = generate("[[1]]\nстарый текст\n")

        self.assertEqual(answer, "[[1]]\nпереписано\n")
        call = client.calls[0]
        self.assertEqual(call["model"], "deepseek-flash")
        self.assertEqual(call["messages"][0], {"role": "system", "content": INSTRUCTIONS})
        self.assertEqual(call["messages"][1], {"role": "user", "content": "[[1]]\nстарый текст\n"})
        self.assertEqual(call["timeout"], DEFAULT_TIMEOUT)

    def test_an_explicit_model_beats_the_provider_default(self) -> None:
        client = FakeClient()
        generate = provider_generator(
            "deepseek", "deepseek-v4-pro", resolve=resolver(client, "deepseek-flash")
        )

        generate("[[1]]\nтекст\n")

        self.assertEqual(client.calls[0]["model"], "deepseek-v4-pro")

    def test_the_generator_says_which_provider_and_model_it_asks(self) -> None:
        client = FakeClient()
        generate = provider_generator("deepseek", resolve=resolver(client, "deepseek-flash"))

        self.assertEqual(generate.description, "deepseek · deepseek-flash")

    def test_a_provider_without_a_client_is_refused(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            provider_generator("deepseek", resolve=lambda provider, model: (None, None))

        self.assertIn("credential", str(caught.exception))

    def test_a_provider_that_names_no_model_is_refused(self) -> None:
        client = FakeClient()
        with self.assertRaises(RuntimeError) as caught:
            provider_generator("deepseek", resolve=resolver(client, ""))

        self.assertIn("--model", str(caught.exception))
        self.assertEqual(client.calls, [])

    def test_an_empty_answer_is_an_error_not_an_empty_rewrite(self) -> None:
        client = FakeClient(text="")
        generate = provider_generator("deepseek", resolve=resolver(client, "deepseek-flash"))

        with self.assertRaises(RuntimeError) as caught:
            generate("[[1]]\nтекст\n")

        self.assertIn("no text", str(caught.exception))


class CredentialEnvironmentTests(unittest.TestCase):
    """Provider keys live in $HERMES_HOME/.env, which only Hermes' CLI reads."""

    def test_a_missing_file_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(load_credential_environment(Path(directory)))

    def test_the_file_fills_in_names_the_environment_lacks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".env").write_text(
                "# a comment\n\nCOMMAHUM_TEST_FRESH=from-the-file\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("COMMAHUM_TEST_FRESH", None)
                load_credential_environment(home)
                self.assertEqual(os.environ.get("COMMAHUM_TEST_FRESH"), "from-the-file")
                os.environ.pop("COMMAHUM_TEST_FRESH", None)

    def test_an_explicit_export_wins_over_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".env").write_text("COMMAHUM_TEST_SET=from-the-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"COMMAHUM_TEST_SET": "from-the-shell"}):
                load_credential_environment(home)
                self.assertEqual(os.environ["COMMAHUM_TEST_SET"], "from-the-shell")

    def test_quoted_values_are_unquoted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".env").write_text("COMMAHUM_TEST_QUOTED='sk-secret'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("COMMAHUM_TEST_QUOTED", None)
                load_credential_environment(home)
                self.assertEqual(os.environ.get("COMMAHUM_TEST_QUOTED"), "sk-secret")
                os.environ.pop("COMMAHUM_TEST_QUOTED", None)


if __name__ == "__main__":
    unittest.main()
