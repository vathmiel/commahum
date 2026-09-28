"""Tests for the command line.

The default has to be safe: reading the file, showing the diff, changing
nothing. Writing is what `--write` is for. The model is injected here, so the
tests never touch the network.
"""

from __future__ import annotations

import io
import os
import signal
import stat
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from commahum.cli import main
from commahum.interrupts import restore_interrupts, trap_interrupts
from commahum.rewrite import INSTRUCTIONS as rewrite_instructions
from commahum.skill import CATALOG, FLOW_READING, SkillError, vendored_skill


class FakeGenerator:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("the generator was called more often than expected")
        return self.replies.pop(0)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_bytes(text.encode())
        return path

    def run_cli(self, argv, generate) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv, generate=generate)
        return code, out.getvalue(), err.getvalue()

    def test_shows_a_diff_and_leaves_the_file_alone(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        generator = FakeGenerator("[[1]]\nновое\n")

        code, out, _ = self.run_cli([str(path)], generator)

        self.assertEqual(code, 0)
        self.assertIn("-# старое", out)
        self.assertIn("+# новое", out)
        self.assertEqual(path.read_bytes(), "# старое\n{ x = 1; }\n".encode())

    def test_applies_the_rewrite_with_write(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        generator = FakeGenerator("[[1]]\nновое\n")

        code, _, _ = self.run_cli(["--write", str(path)], generator)

        self.assertEqual(code, 0)
        self.assertEqual(path.read_bytes(), "# новое\n{ x = 1; }\n".encode())

    def test_replaces_the_file_in_one_step_and_leaves_no_debris(self) -> None:
        # A config file caught halfway through a write is worse than one that was
        # not rewritten at all, so the replacement has to be atomic — and it must
        # not quietly change the file's mode.
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        path.chmod(0o640)
        generator = FakeGenerator("[[1]]\nновое\n")
        original_inode = path.stat().st_ino

        code, _, _ = self.run_cli(["--write", str(path)], generator)

        self.assertEqual(code, 0)
        self.assertEqual(path.read_bytes(), "# новое\n{ x = 1; }\n".encode())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), ["home.nix"])
        # Truncating in place would keep the inode; a replacement gets a new one,
        # so a reader never sees a half-written file.
        self.assertNotEqual(path.stat().st_ino, original_inode)

    def test_dry_run_leaves_the_mode_alone(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        path.chmod(0o640)
        generator = FakeGenerator("[[1]]\nновое\n")

        code, _, _ = self.run_cli([str(path)], generator)

        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_walks_a_directory(self) -> None:
        self.write("a.nix", "# первый\n{}\n")
        self.write("b.nix", "# второй\n{}\n")
        (self.root / "notes.txt").write_bytes(b"# not a nix file\n")
        generator = FakeGenerator("[[1]]\nПЕРВЫЙ\n", "[[1]]\nВТОРОЙ\n")

        code, out, _ = self.run_cli([str(self.root)], generator)

        self.assertEqual(code, 0)
        self.assertIn("+# ПЕРВЫЙ", out)
        self.assertIn("+# ВТОРОЙ", out)

    def test_reports_what_it_left_alone(self) -> None:
        path = self.write("home.nix", "# текст\n# nix-shell -p hello\n{}\n")
        generator = FakeGenerator("[[1]]\nНОВЫЙ\n")

        code, _, err = self.run_cli([str(path)], generator)

        self.assertEqual(code, 0)
        self.assertIn("1 skipped", err)

    def test_reports_a_file_the_model_could_not_handle_and_keeps_going(self) -> None:
        broken = self.write("broken.nix", "# текст\n{}\n")
        ok = self.write("ok.nix", "# текст\n{}\n")

        class FlakyGenerator:
            def __init__(self) -> None:
                self.seen = 0

            def __call__(self, prompt: str) -> str:
                self.seen += 1
                if self.seen == 1:
                    raise RuntimeError("Codex generation failed")
                return "[[1]]\nНОВЫЙ\n"

        code, out, err = self.run_cli([str(broken), str(ok)], FlakyGenerator())

        self.assertEqual(code, 1)
        self.assertIn("broken.nix", err)
        self.assertIn("Codex generation failed", err)
        self.assertIn("+# НОВЫЙ", out)

    def test_reports_a_missing_path(self) -> None:
        code, _, err = self.run_cli([str(self.root / "absent.nix")], FakeGenerator())

        self.assertEqual(code, 1)
        self.assertIn("absent.nix", err)

    def test_reports_comments_the_model_did_not_return(self) -> None:
        path = self.write("home.nix", "# текст\n{}\n")
        generator = FakeGenerator("", "")

        code, _, err = self.run_cli([str(path)], generator)

        self.assertEqual(code, 0)
        self.assertIn("1 kept", err)

    def test_provider_flag_reaches_pi(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        calls: list[tuple[str | None, str | None, str]] = []

        def fake_pi_generator(model, provider, *, instructions, **rest):
            calls.append((model, provider, instructions))
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.asking.pi_generator", fake_pi_generator):
            code, out, _ = self.run_cli([str(path), "--provider", "deepseek"], None)

        self.assertEqual(code, 0)
        self.assertEqual([(model, provider) for model, provider, _ in calls], [(None, "deepseek")])
        self.assertIn("+# новое", out)

    def test_pi_is_given_our_own_instructions(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        seen: list[str] = []

        def fake_pi_generator(model, provider, *, instructions, **rest):
            seen.append(instructions)
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.asking.pi_generator", fake_pi_generator):
            self.run_cli([str(path)], None)

        self.assertEqual(seen, [rewrite_instructions])

    def test_provider_flag_passes_an_explicit_model_through(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        calls: list[tuple[str | None, str | None]] = []

        def fake_pi_generator(model, provider, *, instructions, **rest):
            calls.append((model, provider))
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.asking.pi_generator", fake_pi_generator):
            self.run_cli(
                [str(path), "--provider", "deepseek", "--model", "deepseek/deepseek-v4-pro"],
                None,
            )

        self.assertEqual(calls, [("deepseek/deepseek-v4-pro", "deepseek")])

    def test_without_a_model_pi_is_left_to_choose_one(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        seen: list[tuple[str | None, str | None]] = []

        def fake_pi_generator(model, provider, *, instructions, **rest):
            seen.append((model, provider))
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.asking.pi_generator", fake_pi_generator):
            code, _, _ = self.run_cli([str(path)], None)

        self.assertEqual(code, 0)
        self.assertEqual(seen, [(None, None)])

    def test_a_missing_pi_is_a_clean_refusal_and_not_a_traceback(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")

        def refusing_pi_generator(model, provider, *, instructions, **rest):
            raise RuntimeError("pi is not on PATH: install it or point $COMMAHUM_PI at it")

        with patch("commahum.asking.pi_generator", refusing_pi_generator):
            code, out, err = self.run_cli([str(path)], None)

        self.assertEqual(code, 2)
        self.assertIn("commahum: pi is not on PATH", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")

    def test_reports_where_the_request_goes(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        generator = FakeGenerator("[[1]]\nновое\n")
        generator.description = "deepseek · deepseek-flash"

        code, _, err = self.run_cli([str(path)], generator)

        self.assertEqual(code, 0)
        self.assertIn("home.nix: asking deepseek · deepseek-flash", err)

    def test_the_request_opens_with_the_skill_and_carries_the_flow_reading(self) -> None:
        """The rules travel with every file, not once per run."""
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        generator = FakeGenerator("[[1]]\nновое\n")
        skill = vendored_skill()
        seen: list[dict] = []

        def fake_pi_generator(model, provider, **kwargs):
            seen.append(kwargs)
            return generator

        with patch("commahum.asking.pi_generator", fake_pi_generator):
            code, _, _ = self.run_cli([str(path)], None)

        self.assertEqual(code, 0)
        self.assertEqual(seen[0]["skill"], skill)
        self.assertEqual(seen[0]["readings"], FLOW_READING)
        self.assertTrue(generator.prompts[0].startswith(skill.opening), generator.prompts[0])
        self.assertIn("[[1]]", generator.prompts[0])
        self.assertIn("старое", generator.prompts[0])

    def test_the_catalogue_is_sent_only_when_it_was_asked_for(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        seen: list[dict] = []

        def fake_pi_generator(model, provider, **kwargs):
            seen.append(kwargs)
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.asking.pi_generator", fake_pi_generator):
            self.run_cli(["--catalog", str(path)], None)

        self.assertEqual(seen[0]["readings"], (*FLOW_READING, CATALOG))

    def test_a_missing_skill_is_a_clean_refusal_and_not_a_traceback(self) -> None:
        """No rules means no run: comments are not rewritten by taste."""
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        refusal = SkillError("no vendored skill: expected vendor/humanizer-ru/SKILL.md")

        with patch("commahum.cli.vendored_skill", side_effect=refusal):
            code, out, err = self.run_cli([str(path)], FakeGenerator("[[1]]\nновое\n"))

        self.assertEqual(code, 2)
        self.assertIn("commahum: no vendored skill", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        self.assertEqual(path.read_bytes(), "# старое\n{ x = 1; }\n".encode())

    def test_a_generator_without_a_named_target_still_reports_progress(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        generator = FakeGenerator("[[1]]\nновое\n")

        code, _, err = self.run_cli([str(path)], generator)

        self.assertEqual(code, 0)
        self.assertIn("home.nix: rewriting", err)

    def test_quiet_silences_progress_but_keeps_the_diff_and_the_report(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        generator = FakeGenerator("[[1]]\nновое\n")
        generator.description = "deepseek · deepseek-flash"

        code, out, err = self.run_cli(["--quiet", str(path)], generator)

        self.assertEqual(code, 0)
        self.assertNotIn("asking", err)
        self.assertNotIn("rewriting", err)
        self.assertIn("1 rewritten", err)
        self.assertIn("+# новое", out)


class WriteConflictTests(unittest.TestCase):
    """A rewrite must not land on a file that changed while the model was asked.

    The read and the write are separated by a model request, which can take a
    while, and an editor or another tool can save the file in that window. When
    the file on disk is no longer what the rewrite was computed from, the write
    is refused and reported: clobbering the other change would be worse than
    leaving this file un-rewritten, and a run over many files goes on.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_bytes(text.encode())
        return path

    def run_cli(self, argv, generate) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv, generate=generate)
        return code, out.getvalue(), err.getvalue()

    def test_a_file_that_changed_while_its_rewrite_was_in_flight_is_left_alone(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        path.chmod(0o640)
        edited = "# чужое\n{ x = 2; }\n"

        def generate(_prompt: str) -> str:
            path.write_bytes(edited.encode())  # another tool saves the file mid-request
            return "[[1]]\nновое\n"

        code, out, err = self.run_cli(["--write", str(path)], generate)

        self.assertEqual(code, 1)
        self.assertIn(f"{path}: changed since it was read, not written", err)
        self.assertIn("-# старое", out)  # the rewrite is still shown, just not applied
        self.assertIn("+# новое", out)
        self.assertEqual(path.read_bytes(), edited.encode())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), ["home.nix"])

    def test_a_conflict_does_not_stop_the_files_after_it(self) -> None:
        """One file the run refuses to write must not cost the others.

        The refusal is per file: the run reports it, keeps going, and ends with
        the non-zero status the failure deserves.
        """
        edited = "# чужое\n{ x = 1; }\n"
        conflicted = self.write("a.nix", "# первое\n{ x = 1; }\n")
        later = self.write("b.nix", "# второе\n{ x = 2; }\n")

        def generate(prompt: str) -> str:
            if "первое" in prompt:
                conflicted.write_bytes(edited.encode())  # a save lands mid-request
            return "[[1]]\nНОВОЕ\n"

        code, _, err = self.run_cli(["--write", str(conflicted), str(later)], generate)

        self.assertEqual(code, 1)
        self.assertIn(f"{conflicted}: changed since it was read, not written", err)
        self.assertEqual(conflicted.read_bytes(), edited.encode())
        self.assertIn(f"{later}: 1 rewritten", err)
        self.assertEqual(later.read_bytes(), "# НОВОЕ\n{ x = 2; }\n".encode())
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), ["a.nix", "b.nix"])

    def test_a_concurrent_save_with_the_same_bytes_is_not_a_conflict(self) -> None:
        """The check is on the content, not on the timestamps.

        An editor that saves the file without changing a byte — or a tool that
        rewrites it identically — leaves nothing to lose: the bytes on disk are
        exactly the ones the rewrite was computed from, so the rewrite lands.
        """
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")

        def generate(_prompt: str) -> str:
            path.write_bytes(path.read_bytes())  # a save that changes no byte
            os.utime(path, ns=(time.time_ns() + 1_000_000_000, time.time_ns() + 1_000_000_000))
            return "[[1]]\nновое\n"

        code, _, err = self.run_cli(["--write", str(path)], generate)

        self.assertEqual(code, 0)
        self.assertNotIn("not written", err)
        self.assertEqual(path.read_bytes(), "# новое\n{ x = 1; }\n".encode())

    def test_an_interrupt_while_the_file_is_re_read_before_the_move_leaves_nothing_behind(
        self,
    ) -> None:
        """The check gives the interrupt one more place to land, and it is safe.

        The temporary file is already written when the file is re-read, so an
        interrupt here must cost neither the original nor leave debris next to it.
        """
        path = self.write("home.nix", "# старое\n{ } \n")
        original = path.read_bytes()
        real_read_bytes = Path.read_bytes
        armed = {"yes": False}

        def generate(_prompt: str) -> str:
            armed["yes"] = True
            return "[[1]]\nновое\n"

        def read_bytes(self):
            if armed["yes"] and self == path:
                raise KeyboardInterrupt
            return real_read_bytes(self)

        with patch.object(Path, "read_bytes", read_bytes):
            code, _, _ = self.run_cli(["--write", str(path)], generate)

        self.assertEqual(code, 130)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(sorted(self.root.iterdir()), [path])

    def test_a_file_that_vanished_while_it_was_rewritten_is_not_recreated(self) -> None:
        """A file that is gone is not a file to write: fail closed and report it.

        Writing anyway would resurrect a file someone else just deleted, and
        crashing on it would be a worse answer than saying what happened.
        """
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")

        def generate(_prompt: str) -> str:
            path.unlink()  # another process removes the file mid-request
            return "[[1]]\nновое\n"

        code, _, err = self.run_cli(["--write", str(path)], generate)

        self.assertEqual(code, 1)
        self.assertIn(f"{path}: gone since it was read, not written", err)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_file_that_vanishes_between_the_reads_is_not_written(self) -> None:
        """A file can also vanish after its mode was read.

        The re-read before the move finds it gone, refuses the write the same
        way, and takes the temporary file with it.
        """
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        real_read_bytes = Path.read_bytes
        armed = {"yes": False}

        def generate(_prompt: str) -> str:
            armed["yes"] = True
            return "[[1]]\nновое\n"

        def read_bytes(self):
            if armed["yes"] and self == path:
                self.unlink()
            return real_read_bytes(self)

        with patch.object(Path, "read_bytes", read_bytes):
            code, _, err = self.run_cli(["--write", str(path)], generate)

        self.assertEqual(code, 1)
        self.assertIn(f"{path}: gone since it was read, not written", err)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])


class InterruptTests(unittest.TestCase):
    """Stopping a run must not cost a file. Every file is either rewritten or
    untouched, the run says how far it got, and the shell learns why it stopped."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_bytes(text.encode())
        return path

    def run_cli(self, argv, generate) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv, generate=generate)
        return code, out.getvalue(), err.getvalue()

    def interrupting_generator(self, first_reply: str):
        """Answers the first file, then interrupts the run from inside the second."""
        answered = FakeGenerator(first_reply)

        def generate(prompt: str) -> str:
            if answered.prompts:
                raise KeyboardInterrupt
            return answered(prompt)

        return generate

    def test_an_interrupt_stops_before_the_next_file_and_reports_the_progress(self) -> None:
        first = self.write("a.nix", "# первое\n{ }\n")
        second = self.write("b.nix", "# второе\n{ }\n")

        code, _, err = self.run_cli(
            [str(first), str(second)], self.interrupting_generator("[[1]]\nновое\n")
        )

        self.assertEqual(code, 130)
        self.assertIn("interrupted: SIGINT, stopped after 1 of 2 files", err)
        self.assertIn("1 files, 1 comments rewritten", err)
        self.assertEqual(second.read_bytes(), "# второе\n{ }\n".encode())

    def test_an_interrupted_write_keeps_the_files_it_finished(self) -> None:
        first = self.write("a.nix", "# первое\n{ }\n")
        second = self.write("b.nix", "# второе\n{ }\n")

        code, _, err = self.run_cli(
            ["--write", str(first), str(second)],
            self.interrupting_generator("[[1]]\nновое\n"),
        )

        self.assertEqual(code, 130)
        self.assertIn("1 written", err)
        self.assertEqual(first.read_bytes(), "# новое\n{ }\n".encode())
        self.assertEqual(second.read_bytes(), "# второе\n{ }\n".encode())

    def test_termination_and_hang_up_are_treated_like_an_interrupt(self) -> None:
        path = self.write("home.nix", "# старое\n{ }\n")

        for signum, expected in ((signal.SIGTERM, 143), (signal.SIGHUP, 129)):
            with self.subTest(signum=signum.name):

                def generate(_prompt: str, signum=signum) -> str:
                    os.kill(os.getpid(), signum)
                    raise AssertionError("the signal did not interrupt the run")

                code, _, err = self.run_cli([str(path)], generate)

                self.assertEqual(code, expected)
                self.assertIn(f"interrupted: {signum.name}", err)
                self.assertNotIn("Traceback", err)

    def test_a_second_interrupt_leaves_immediately(self) -> None:
        state = trap_interrupts()
        self.addCleanup(restore_interrupts, state)
        handler = signal.getsignal(signal.SIGINT)

        with self.assertRaises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
        with patch("commahum.interrupts.os._exit") as exit_now:
            handler(signal.SIGINT, None)

        exit_now.assert_called_once_with(130)

    def test_a_broken_pipe_ends_the_run_without_a_traceback(self) -> None:
        path = self.write("home.nix", "# старое\n{ }\n")

        class ClosedPipe(io.StringIO):
            def write(self, _text: str) -> int:
                raise BrokenPipeError(32, "Broken pipe")

        err = io.StringIO()
        with redirect_stdout(ClosedPipe()), redirect_stderr(err):
            code = main([str(path)], generate=FakeGenerator("[[1]]\nновое\n"))

        self.assertEqual(code, 141)
        self.assertNotIn("Traceback", err.getvalue())

    def test_an_interrupt_between_the_temp_file_and_the_move_leaves_nothing_behind(
        self,
    ) -> None:
        path = self.write("home.nix", "# старое\n{ }\n")
        original = path.read_bytes()

        with patch("commahum.cli.os.replace", side_effect=KeyboardInterrupt):
            code, _, _ = self.run_cli(
                ["--write", str(path)], FakeGenerator("[[1]]\nновое\n")
            )

        self.assertEqual(code, 130)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(sorted(self.root.iterdir()), [path])

    def test_no_handlers_are_installed_from_a_thread(self) -> None:
        """Signals belong to the main thread; a threaded caller keeps its own."""
        state: list[dict] = []

        def install() -> None:
            state.append(trap_interrupts())
            restore_interrupts(state[-1])

        thread = threading.Thread(target=install)
        thread.start()
        thread.join()

        self.assertEqual(state[0]["previous"], {})

    def test_a_bare_path_still_means_rewrite(self) -> None:
        """The everyday invocation stays short; naming the command is optional."""
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")

        code, out, err = self.run_cli([str(path)], FakeGenerator("[[1]]\nновое\n"))

        self.assertEqual(code, 0)
        self.assertIn("-# старое", out)
        self.assertIn("1 files, 1 comments rewritten", err)

    def test_the_command_may_be_named_explicitly(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")

        code, out, _ = self.run_cli(["rewrite", str(path)], FakeGenerator("[[1]]\nновое\n"))

        self.assertEqual(code, 0)
        self.assertIn("+# новое", out)
        self.assertIn("-# старое", out)

    def test_the_help_lists_every_command(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            main(["--help"])

        self.assertEqual(caught.exception.code, 0)
        for name in ("rewrite", "report", "probe", "check"):
            self.assertIn(name, out.getvalue())

    def test_no_arguments_at_all_prints_the_same_help(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            main([])

        self.assertEqual(caught.exception.code, 0)
        self.assertIn("usage: commahum", out.getvalue())

    def test_a_file_named_like_a_command_is_reachable_by_path(self) -> None:
        """`commahum report` is the command; the file of that name is `./report`.

        The price of keeping the everyday invocation short is exactly this, so it
        is pinned: no clever guessing at which one the user meant.
        """
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            main(["report"])

        self.assertEqual(caught.exception.code, 2)
        self.assertIn("paths", err.getvalue())


class ReportCommandTests(unittest.TestCase):
    """`report` runs the pipeline, then hands the prose to the skill's own scanner."""

    def setUp(self) -> None:
        try:
            import pymorphy3  # noqa: F401
            import razdel  # noqa: F401
        except ImportError:  # pragma: no cover - the extra is optional
            self.skipTest("the `style` extra is not installed, so the scanner cannot run")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_the_scanner_grades_the_rewrite(self) -> None:
        path = self.root / "home.nix"
        path.write_bytes("# Комментарий, который был канцеляритом.\n{ x = 1; }\n".encode())
        generator = FakeGenerator("[[1]]\nКомментарий проще.\n")

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["report", str(path)], generate=generator)

        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("1 rewritten", err.getvalue())
        self.assertIn("ЧИСТОТА", out.getvalue())


if __name__ == "__main__":
    unittest.main()
