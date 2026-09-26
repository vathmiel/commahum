"""The probe's instruments, tested without asking a model anything.

What the probe does on the wire is checked live, by running it. What can be checked
here is the part that makes its answer mean something: the copy it hands over
carries a token that exists nowhere else, the question asks for that token, and a
model that answers with something other than the token fails the run.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commahum import probe
from commahum.cli import main

BODY_TOKEN = "ТЕЛО-0000dead"
READING_TOKEN = "ДОПОЛНЕНИЕ-0000beef"
ABSENT_TOKEN = "ОТСУТСТВУЕТ-0000cafe"


class RiggedCopyTests(unittest.TestCase):
    def test_the_copy_carries_the_tokens_where_the_probe_says(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            skill = probe.rigged(Path(directory), {probe.BODY: BODY_TOKEN, probe.READING: READING_TOKEN})

            self.assertEqual(skill.name, "humanizer-ru")
            self.assertIn(BODY_TOKEN, (skill.directory / probe.BODY).read_text(encoding="utf-8"))
            self.assertIn(
                READING_TOKEN, (skill.directory / probe.READING).read_text(encoding="utf-8")
            )

    def test_the_copy_does_not_disturb_the_vendored_one(self) -> None:
        vendored = probe.vendored_skill()
        before = (vendored.directory / probe.BODY).read_bytes()

        with tempfile.TemporaryDirectory() as directory:
            probe.rigged(Path(directory), {probe.BODY: BODY_TOKEN})

        self.assertEqual((vendored.directory / probe.BODY).read_bytes(), before)

    def test_the_tokens_are_new_on_every_run(self) -> None:
        self.assertEqual(len(set(probe.tokens())), 3)
        self.assertNotEqual(probe.tokens(), probe.tokens())


class ProbeCommandTests(unittest.TestCase):
    def run_probe(self, answer: str, negative: str) -> tuple[int, str]:
        def generate(prompt: str) -> str:
            return negative if ABSENT_TOKEN in prompt else answer

        out = io.StringIO()
        with patch.object(probe, "tokens", return_value=(BODY_TOKEN, READING_TOKEN, ABSENT_TOKEN)):
            with contextlib.redirect_stdout(out):
                status = main(["probe"], generate=generate)
        return status, out.getvalue()

    def test_all_checks_pass_when_the_tokens_come_back(self) -> None:
        status, out = self.run_probe(
            f"КОНТРОЛЬНАЯ СТРОКА: {BODY_TOKEN}\nКОНТРОЛЬНАЯ СТРОКА: {READING_TOKEN}",
            "нет",
        )

        self.assertEqual(status, 0, out)
        self.assertIn(f"{BODY_TOKEN}", out)

    def test_a_model_that_answers_nothing_fails_the_run(self) -> None:
        status, out = self.run_probe("нет", "нет")

        self.assertEqual(status, 1, out)
        self.assertIn("FAIL", out)

    def test_a_model_that_invents_the_absent_token_fails_the_run(self) -> None:
        """The rigging is only evidence if reciting everything is punished."""
        status, out = self.run_probe(
            f"КОНТРОЛЬНАЯ СТРОКА: {BODY_TOKEN}\nКОНТРОЛЬНАЯ СТРОКА: {READING_TOKEN}",
            f"КОНТРОЛЬНАЯ СТРОКА: {ABSENT_TOKEN}",
        )

        self.assertEqual(status, 1, out)
        self.assertIn("FAIL", out)


if __name__ == "__main__":
    unittest.main()
