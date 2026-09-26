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
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from commahum.cli import _restore_interrupts, _trap_interrupts, codex_generator, main
from commahum.codex_adapter import DEFAULT_MODEL


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

    def test_provider_flag_routes_to_the_vendored_layer(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        calls: list[tuple[str, str | None]] = []

        def fake_provider_generator(provider, model=None):
            calls.append((provider, model))
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.cli.provider_generator", fake_provider_generator):
            code, out, _ = self.run_cli([str(path), "--provider", "deepseek"], None)

        self.assertEqual(code, 0)
        self.assertEqual(calls, [("deepseek", None)])
        self.assertIn("+# новое", out)

    def test_provider_flag_passes_an_explicit_model_through(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        calls: list[tuple[str, str | None]] = []

        def fake_provider_generator(provider, model=None):
            calls.append((provider, model))
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.cli.provider_generator", fake_provider_generator):
            self.run_cli(
                [str(path), "--provider", "deepseek", "--model", "deepseek-v4-pro"], None
            )

        self.assertEqual(calls, [("deepseek", "deepseek-v4-pro")])

    def test_without_a_provider_the_codex_default_model_is_asked(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        seen: list[str] = []

        def fake_codex_generator(model=DEFAULT_MODEL):
            seen.append(model)
            return FakeGenerator("[[1]]\nновое\n")

        with patch("commahum.cli.codex_generator", fake_codex_generator):
            code, _, _ = self.run_cli([str(path)], None)

        self.assertEqual(code, 0)
        self.assertEqual(seen, [DEFAULT_MODEL])

    def test_reports_where_the_request_goes(self) -> None:
        path = self.write("home.nix", "# старое\n{ x = 1; }\n")
        generator = FakeGenerator("[[1]]\nновое\n")
        generator.description = "deepseek · deepseek-flash"

        code, _, err = self.run_cli([str(path)], generator)

        self.assertEqual(code, 0)
        self.assertIn("home.nix: asking deepseek · deepseek-flash", err)

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

    def test_the_codex_generator_names_its_target(self) -> None:
        with patch("commahum.codex_adapter.CodexSubscriptionClient"), patch(
            "commahum.codex_adapter.HermesTokenStore"
        ):
            generate = codex_generator("gpt-5.5")

        self.assertEqual(generate.description, "Codex subscription · gpt-5.5")


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
        state = _trap_interrupts()
        self.addCleanup(_restore_interrupts, state)
        handler = signal.getsignal(signal.SIGINT)

        with self.assertRaises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
        with patch("commahum.cli.os._exit") as exit_now:
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
            state.append(_trap_interrupts())
            _restore_interrupts(state[-1])

        thread = threading.Thread(target=install)
        thread.start()
        thread.join()

        self.assertEqual(state[0]["previous"], {})


if __name__ == "__main__":
    unittest.main()
