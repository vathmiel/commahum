"""Tests for the pi-backed model call.

pi owns the providers, so this module owns the contract with pi instead: a
hermetic invocation, the prose on stdin rather than in the process table,
failures that say what pi said, and a pi that can always be found — the one
vendored in this checkout first. The runner is injected, so no test here starts a
process or touches the network.

The skill is part of that contract: every request loads it by name and carries
the files its flow reads, and a prompt that does not invoke it is refused before
pi is started at all.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from commahum.pi_runner import (
    ENVIRONMENT,
    ISOLATION,
    VENDORED_ENTRY,
    build_argv,
    pi_command,
    pi_generator,
    vendored_entry,
)
from commahum.skill import FLOW_READING, SkillError, vendored_skill

INSTRUCTIONS = "Правила редактуры."
INVOCATION = vendored_skill().invocation
OPENING = vendored_skill().opening


def asking(text: str) -> str:
    """A prompt shaped the way the pipeline shapes one: the skill first, then prose."""
    return f"{OPENING}Комментарии:\n[[1]]\n{text}\n"


def completed(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    return subprocess.CompletedProcess(args=["pi"], returncode=returncode, stdout=stdout, stderr=stderr)


class RecordingRun:
    """Stands in for ``subprocess.run`` and remembers what it was asked to do."""

    def __init__(self, result=None) -> None:
        self.result = result if result is not None else completed("готово\n".encode())
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return self.result

    @property
    def argv(self) -> list[str]:
        return self.calls[0][0]

    @property
    def kwargs(self) -> dict:
        return self.calls[0][1]


class ArgumentTests(unittest.TestCase):
    """What pi is told to be, before it is told anything about the prose."""

    def test_the_system_prompt_carries_our_instructions(self) -> None:
        argv = build_argv(["pi"], model=None, provider=None, instructions=INSTRUCTIONS)

        self.assertEqual(argv[-2:], ["--system-prompt", INSTRUCTIONS])

    def test_the_run_is_hermetic(self) -> None:
        argv = build_argv(["pi"], instructions=INSTRUCTIONS)

        for flag in ISOLATION:
            self.assertIn(flag, argv)

    def test_print_mode_is_asked_for(self) -> None:
        argv = build_argv(["pi"], instructions=INSTRUCTIONS)

        self.assertIn("--print", argv)

    def test_a_command_of_several_words_is_kept_whole(self) -> None:
        """The vendored copy is run as `node vendor/pi/dist/bundle/cli.js`."""
        argv = build_argv(["node", "/repo/vendor/pi/dist/bundle/cli.js"], instructions=INSTRUCTIONS)

        self.assertEqual(argv[:3], ["node", "/repo/vendor/pi/dist/bundle/cli.js", "--print"])

    def test_a_provider_and_a_model_are_passed_through_as_pi_understands_them(self) -> None:
        argv = build_argv(
            ["pi"],
            model="deepseek/deepseek-flash",
            provider="deepseek",
            instructions=INSTRUCTIONS,
        )

        self.assertIn("--provider", argv)
        self.assertEqual(argv[argv.index("--provider") + 1], "deepseek")
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "deepseek/deepseek-flash")

    def test_neither_flag_appears_when_nothing_was_asked_for(self) -> None:
        argv = build_argv(["pi"], instructions=INSTRUCTIONS)

        self.assertNotIn("--provider", argv)
        self.assertNotIn("--model", argv)


class CallTests(unittest.TestCase):
    """One request, made as one process."""

    def generator(self, run, **kwargs):
        kwargs.setdefault("instructions", INSTRUCTIONS)
        with patch("commahum.pi_runner.pi_command", return_value=["/nix/store/x/bin/pi"]):
            return pi_generator(run=run, **kwargs)

    def test_the_prose_goes_to_stdin_and_not_to_the_process_table(self) -> None:
        run = RecordingRun()
        generate = self.generator(run)

        prompt = asking("# комментарий")

        generate(prompt)

        self.assertNotIn("# комментарий\n", run.argv)
        self.assertEqual(run.kwargs["input"], prompt.encode("utf-8", "surrogateescape"))
        self.assertNotIn(prompt, run.argv)

    def test_the_answer_is_the_text_pi_printed(self) -> None:
        run = RecordingRun(completed("[[1]]\nновое\n".encode()))

        self.assertEqual(self.generator(run)(asking("# старое")), "[[1]]\nновое\n")

    def test_output_that_is_not_valid_utf8_survives_the_round_trip(self) -> None:
        run = RecordingRun(completed(b"# \xff\xfe\n"))

        answer = self.generator(run)(asking("# x"))

        self.assertEqual(answer.encode("utf-8", "surrogateescape"), b"# \xff\xfe\n")

    def test_the_loud_switches_are_off_and_the_rest_of_the_environment_is_ours(self) -> None:
        run = RecordingRun()

        self.generator(run)(asking("# x"))

        environment = run.kwargs["env"]
        for name, value in ENVIRONMENT.items():
            self.assertEqual(environment[name], value)
        self.assertEqual(environment["PATH"], os.environ["PATH"])

    def test_a_slow_answer_is_a_readable_failure_and_not_a_hang(self) -> None:
        def timing_out(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        generate = self.generator(timing_out)

        with self.assertRaises(RuntimeError) as raised:
            generate(asking("# x"))

        self.assertIn("did not answer", str(raised.exception))

    def test_the_generator_names_its_target(self) -> None:
        run = RecordingRun()

        named = self.generator(run, model="deepseek-flash", provider="deepseek")
        unnamed = self.generator(run)

        self.assertEqual(named.description, "pi · deepseek/deepseek-flash · humanizer-ru")
        self.assertEqual(unnamed.description, "pi · model from pi's config · humanizer-ru")

    def test_a_model_that_already_names_its_provider_is_not_named_twice(self) -> None:
        run = RecordingRun()

        named = self.generator(run, model="deepseek/deepseek-flash", provider="deepseek")

        self.assertEqual(named.description, "pi · deepseek/deepseek-flash · humanizer-ru")

    def test_a_provider_alone_does_not_claim_a_model_it_is_not_calling(self) -> None:
        """pi answers with the model it is configured with, whatever provider was named."""
        run = RecordingRun()

        named = self.generator(run, provider="deepseek")

        self.assertEqual(named.description, "pi · deepseek (model from pi's config) · humanizer-ru")


class FailureTests(unittest.TestCase):
    """When pi refuses, the reason has to survive to the person reading the run."""

    def failing(self, result):
        with patch("commahum.pi_runner.pi_command", return_value=["/nix/store/x/bin/pi"]):
            return pi_generator(instructions=INSTRUCTIONS, run=RecordingRun(result))

    def test_the_failure_repeats_pis_own_words(self) -> None:
        generate = self.failing(
            completed(stderr=b"Error: unknown provider 'deapseek'\n", returncode=1)
        )

        with self.assertRaises(RuntimeError) as raised:
            generate(asking("# x"))

        message = str(raised.exception)
        self.assertIn("exited 1", message)
        self.assertIn("unknown provider 'deapseek'", message)

    def test_a_credential_failure_points_at_pi_auth(self) -> None:
        generate = self.failing(
            completed(stderr=b"Error: no API key found for provider deepseek\n", returncode=1)
        )

        with self.assertRaises(RuntimeError) as raised:
            generate(asking("# x"))

        self.assertIn("pi auth check", str(raised.exception))

    def test_a_failure_of_another_kind_does_not_blame_credentials(self) -> None:
        generate = self.failing(completed(stderr=b"Error: context length exceeded\n", returncode=2))

        with self.assertRaises(RuntimeError) as raised:
            generate(asking("# x"))

        self.assertNotIn("pi auth check", str(raised.exception))

    def test_silence_on_stderr_is_still_reported(self) -> None:
        generate = self.failing(completed(returncode=137))

        with self.assertRaises(RuntimeError) as raised:
            generate(asking("# x"))

        self.assertIn("no output", str(raised.exception))


class SkillTests(unittest.TestCase):
    """The rules travel with the request, or the request does not leave."""

    def generator(self, run, **kwargs):
        kwargs.setdefault("instructions", INSTRUCTIONS)
        with patch("commahum.pi_runner.pi_command", return_value=["/nix/store/x/bin/pi"]):
            return pi_generator(run=run, **kwargs)

    @property
    def skill(self):
        return vendored_skill()

    def test_the_vendored_skill_is_handed_to_pi_by_path(self) -> None:
        run = RecordingRun()

        self.generator(run)(asking("# x"))

        self.assertIn("--skill", run.argv)
        self.assertEqual(run.argv[run.argv.index("--skill") + 1], str(self.skill.directory))

    def test_discovery_is_off_so_the_rules_are_the_ones_this_checkout_pinned(self) -> None:
        """`--skill` is explicit; whatever else the host has installed is not read."""
        run = RecordingRun()

        self.generator(run)(asking("# x"))

        self.assertIn("--no-skills", run.argv)

    def test_the_files_the_flow_reads_travel_with_the_request(self) -> None:
        run = RecordingRun()

        self.generator(run)(asking("# x"))

        appended = [
            run.argv[index + 1]
            for index, argument in enumerate(run.argv)
            if argument == "--append-system-prompt"
        ]
        self.assertEqual(
            appended,
            [str(self.skill.file(relative)) for relative in FLOW_READING],
        )
        for path in appended:
            self.assertTrue(Path(path).is_file(), path)

    def test_the_flow_reading_is_edit_log_then_the_review_brief(self) -> None:
        """The two files the skill names: read before the edit, and for the cold re-read."""
        self.assertEqual(FLOW_READING, ("edit-log.md", "references/review.md"))
        self.assertIn("Журнал живого фидбека", self.skill.reading("edit-log.md"))
        self.assertIn("чистовик", self.skill.reading("references/review.md").lower())

    def test_a_prompt_that_does_not_invoke_the_skill_is_refused(self) -> None:
        run = RecordingRun()
        generate = self.generator(run)

        with self.assertRaises(RuntimeError) as raised:
            generate("Комментарии:\n[[1]]\n# x\n")

        self.assertIn(self.skill.opening, str(raised.exception))
        self.assertEqual(run.calls, [], "pi was started for a request with no rules")

    def test_the_skill_name_has_to_end_at_a_space(self) -> None:
        """pi takes the name to be everything up to the first space in the prompt.

        A newline straight after the name makes that name `humanizer-ru\\nКомментарии:`,
        which matches no skill, and pi then passes the prompt through untouched —
        no error, no warning, and a run with no rules at all. This is that shape.
        """
        run = RecordingRun()
        generate = self.generator(run)

        with self.assertRaises(RuntimeError):
            generate(f"{self.skill.invocation}\nКомментарии:\n[[1]]\n# x\n")

        self.assertEqual(run.calls, [])

    def test_a_leading_blank_line_is_refused_rather_than_trimmed(self) -> None:
        """pi expands a command only when the prompt starts with it, character one."""
        run = RecordingRun()
        generate = self.generator(run)

        with self.assertRaises(RuntimeError):
            generate("\n" + asking("# x"))

        self.assertEqual(run.calls, [])

    def test_the_catalogue_can_be_asked_for(self) -> None:
        run = RecordingRun()

        self.generator(run, readings=(*FLOW_READING, "references/catalog.md"))(asking("# x"))

        appended = [
            run.argv[index + 1]
            for index, argument in enumerate(run.argv)
            if argument == "--append-system-prompt"
        ]
        self.assertEqual(len(appended), 3)
        self.assertTrue(appended[-1].endswith("references/catalog.md"))

    def test_a_companion_file_that_is_not_there_is_an_error_and_not_a_skip(self) -> None:
        run = RecordingRun()

        with self.assertRaises(SkillError):
            self.generator(run, readings=("references/nowhere.md",))

        self.assertEqual(run.calls, [])


class CommandTests(unittest.TestCase):
    """Where pi comes from: this checkout first, then the host."""

    def test_the_vendored_copy_is_what_this_checkout_runs(self) -> None:
        entry = vendored_entry()

        self.assertIsNotNone(entry, "vendor/pi/ is missing from this checkout")
        assert entry is not None
        self.assertEqual(entry.parts[-len(VENDORED_ENTRY):], VENDORED_ENTRY)
        self.assertTrue(entry.is_file())

    def test_the_vendored_copy_is_run_with_node(self) -> None:
        entry = Path("/repo/vendor/pi/dist/bundle/cli.js")
        with patch.dict(os.environ, {}, clear=True), patch(
            "commahum.pi_runner.vendored_entry", return_value=entry
        ), patch("commahum.pi_runner.shutil.which", return_value="/usr/bin/node"):
            self.assertEqual(pi_command(), ["/usr/bin/node", str(entry)])

    def test_a_vendored_copy_without_node_says_so(self) -> None:
        entry = Path("/repo/vendor/pi/dist/bundle/cli.js")
        with patch.dict(os.environ, {}, clear=True), patch(
            "commahum.pi_runner.vendored_entry", return_value=entry
        ), patch("commahum.pi_runner.shutil.which", return_value=None), self.assertRaises(
            RuntimeError
        ) as raised:
            pi_command()

        self.assertIn("node", str(raised.exception))

    def test_the_override_wins_over_everything(self) -> None:
        with patch.dict(os.environ, {"COMMAHUM_PI": "/opt/pi/bin/pi"}), patch(
            "commahum.pi_runner.vendored_entry"
        ) as vendored:
            self.assertEqual(pi_command(), ["/opt/pi/bin/pi"])

        vendored.assert_not_called()

    def test_a_pi_on_the_path_is_the_last_resort(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "commahum.pi_runner.vendored_entry", return_value=None
        ), patch("commahum.pi_runner.shutil.which", return_value="/usr/bin/pi"):
            self.assertEqual(pi_command(), ["/usr/bin/pi"])

    def test_nothing_to_run_says_how_to_get_one(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "commahum.pi_runner.vendored_entry", return_value=None
        ), patch("commahum.pi_runner.shutil.which", return_value=None), self.assertRaises(
            RuntimeError
        ) as raised:
            pi_command()

        self.assertIn("COMMAHUM_PI", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
