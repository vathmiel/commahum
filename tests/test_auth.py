"""`auth` says where a credential comes from, and stores one only on request.

Two things are checked here without touching the live store and without a network:
that the source is worked out from two questions to pi rather than guessed, and that
a store write is either confirmed by pi or put back as it was. The store path is
redirected with pi's own variable, and pi itself is injected — the one call that has
to be real, the final `auth check`, is verified live instead.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from commahum import auth
from commahum.auth import AuthError
from commahum.cli import main

KEY = "sk-тест-0123456789abcdef"
OTHER_KEY = "sk-чужой-0123456789abcdef"


class FakeChecker:
    """pi's answers, keyed by whether the provider variables are present."""

    def __init__(self, *, as_is: str = "ready", bare: str = "ready", reason: str = "credentials_not_configured") -> None:
        self.as_is = as_is
        self.bare = bare
        self.reason = reason
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, provider: str, *, environment: dict | None = None) -> dict:
        clean = environment is not None
        self.calls.append((provider, clean))
        status = self.bare if clean else self.as_is
        if status == "ready":
            return {"status": "ready", "provider": provider, "authType": "api_key"}
        return {"status": status, "provider": provider, "reason": self.reason}


class AuthTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = self.root / "agent"
        redirect = patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(self.agent)})
        redirect.start()
        self.addCleanup(redirect.stop)
        self.store = self.agent / "auth.json"

    def run_auth(self, *argv: str, checker=None, stdin: str | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        stream = io.StringIO(stdin) if stdin is not None else io.StringIO("")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with patch("commahum.auth.check", checker or FakeChecker()), patch("sys.stdin", stream):
                code = main(["auth", *argv])
        return code, out.getvalue(), err.getvalue()


class SourceTests(unittest.TestCase):
    def test_a_ready_provider_in_a_bare_environment_is_the_store(self) -> None:
        source, verdict = auth.source_of("deepseek", checker=FakeChecker())

        self.assertEqual(source, "store")
        self.assertEqual(verdict["authType"], "api_key")

    def test_ready_only_with_the_shell_is_the_environment(self) -> None:
        source, _ = auth.source_of("anthropic", checker=FakeChecker(bare="not_ready"))

        self.assertEqual(source, "environment")

    def test_not_ready_stays_not_ready(self) -> None:
        source, verdict = auth.source_of("nobody", checker=FakeChecker(as_is="not_ready"))

        self.assertEqual(source, "none")
        self.assertEqual(verdict["reason"], "credentials_not_configured")

    def test_a_second_answer_that_is_not_a_verdict_is_not_claimed_as_one(self) -> None:
        source, _ = auth.source_of("deepseek", checker=FakeChecker(bare="unknown"))

        self.assertEqual(source, "unknown")

    def test_the_provider_comes_from_the_model_when_it_is_named_there(self) -> None:
        class Arguments:
            provider = None
            model = "deepseek/deepseek-flash"

        self.assertEqual(auth.provider_of(Arguments()), "deepseek")

    def test_a_model_without_a_provider_names_none(self) -> None:
        class Arguments:
            provider = None
            model = "sonnet:high"

        self.assertIsNone(auth.provider_of(Arguments()))


class StorePathTests(unittest.TestCase):
    def test_pi_s_own_variable_wins(self) -> None:
        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": "/tmp/elsewhere"}):
            self.assertEqual(auth.store_path(), Path("/tmp/elsewhere/auth.json"))

    def test_without_the_variable_it_is_the_agent_directory(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=Path("/home/someone")):
            self.assertEqual(auth.store_path(), Path("/home/someone/.pi/agent/auth.json"))


class KeyTests(unittest.TestCase):
    def test_the_first_key_shaped_line_wins_when_no_name_is_asked_for(self) -> None:
        self.assertEqual(auth.key_from("OTHER=1\nDEEPSEEK_API_KEY=sk-abc\n"), "1")

    def test_a_named_line_is_the_one_that_counts(self) -> None:
        self.assertEqual(auth.key_from("OTHER=1\nDEEPSEEK_API_KEY=sk-abc\n", name="DEEPSEEK_API_KEY"), "sk-abc")

    def test_a_name_that_is_not_there_is_named_in_the_error(self) -> None:
        with self.assertRaises(AuthError) as caught:
            auth.key_from("OTHER=1\n", name="DEEPSEEK_API_KEY")

        self.assertIn("DEEPSEEK_API_KEY", str(caught.exception))

    def test_a_bare_key_is_the_key(self) -> None:
        self.assertEqual(auth.key_from("\n  sk-abc  \n"), "sk-abc")

    def test_comments_and_blanks_are_skipped(self) -> None:
        self.assertEqual(auth.key_from("# a comment\n\nsk-abc\n"), "sk-abc")

    def test_quotes_come_off(self) -> None:
        self.assertEqual(auth.key_from('DEEPSEEK_API_KEY="sk-abc"'), "sk-abc")

    def test_nothing_to_read_is_an_error_not_an_empty_credential(self) -> None:
        with self.assertRaises(AuthError):
            auth.key_from("\n# nothing here\n")


class ReportTests(AuthTestCase):
    def test_the_store_is_named_with_its_mode(self) -> None:
        code, out, _ = self.run_auth("--provider", "deepseek", checker=FakeChecker())

        self.assertEqual(code, 0)
        self.assertIn("deepseek: ready", out)
        self.assertIn("pi's store", out)
        self.assertIn(str(self.store), out)

    def test_the_environment_is_named_when_that_is_where_it_comes_from(self) -> None:
        code, out, _ = self.run_auth("--provider", "anthropic", checker=FakeChecker(bare="not_ready"))

        self.assertEqual(code, 0)
        self.assertIn("from the environment", out)

    def test_a_provider_without_a_credential_says_what_to_do(self) -> None:
        code, out, _ = self.run_auth("--provider", "nobody", checker=FakeChecker(as_is="not_ready"))

        self.assertEqual(code, 1)
        self.assertIn("not ready — credentials_not_configured", out)
        self.assertIn("commahum auth --store nobody", out)

    def test_an_empty_store_is_reported_rather_than_looked_past(self) -> None:
        code, out, _ = self.run_auth(checker=FakeChecker())

        self.assertEqual(code, 1)
        self.assertIn("store is empty", out)

    def test_without_a_named_provider_every_stored_one_is_asked_about(self) -> None:
        self.agent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps({"deepseek": {"type": "api_key", "key": KEY}}), encoding="utf-8")
        checker = FakeChecker()

        code, out, _ = self.run_auth(checker=checker)

        self.assertEqual(code, 0)
        self.assertIn("deepseek: ready", out)
        self.assertEqual([name for name, _ in checker.calls], ["deepseek", "deepseek"])

    def test_from_env_without_store_is_refused(self) -> None:
        code, _, err = self.run_auth("--from-env", "DEEPSEEK_API_KEY", checker=FakeChecker())

        self.assertEqual(code, 2)
        self.assertIn("belong with --store", err)


class StoreTests(AuthTestCase):
    def write_store(self, data) -> None:
        self.agent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(data), encoding="utf-8")

    def test_a_confirmed_key_lands_in_the_store_with_mode_six_hundred(self) -> None:
        code, out, err = self.run_auth("--store", "deepseek", checker=FakeChecker(), stdin=f"DEEPSEEK_API_KEY={KEY}\n")

        self.assertEqual(code, 0, err)
        written = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(written, {"deepseek": {"type": "api_key", "key": KEY}})
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)

    def test_the_key_is_never_printed(self) -> None:
        _, out, err = self.run_auth("--store", "deepseek", checker=FakeChecker(), stdin=KEY)

        self.assertNotIn(KEY, out + err)
        self.assertIn(f"{len(KEY)} characters", out)

    def test_a_key_pi_refuses_is_rolled_back(self) -> None:
        self.write_store({"anthropic": {"type": "api_key", "key": OTHER_KEY}})
        before = self.store.read_bytes()

        code, _, err = self.run_auth("--store", "deepseek", checker=FakeChecker(bare="not_ready"), stdin=KEY)

        self.assertEqual(code, 2)
        self.assertIn("did not confirm", err)
        self.assertIn("back as it was", err)
        self.assertEqual(self.store.read_bytes(), before)

    def test_a_key_pi_refuses_does_not_leave_a_store_that_was_not_there(self) -> None:
        code, _, _ = self.run_auth("--store", "deepseek", checker=FakeChecker(bare="not_ready"), stdin=KEY)

        self.assertEqual(code, 2)
        self.assertFalse(self.store.exists())

    def test_other_providers_are_kept(self) -> None:
        self.write_store({"anthropic": {"type": "api_key", "key": OTHER_KEY}})

        code, _, err = self.run_auth("--store", "deepseek", checker=FakeChecker(), stdin=KEY)

        self.assertEqual(code, 0, err)
        written = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(sorted(written), ["anthropic", "deepseek"])
        self.assertEqual(written["anthropic"]["key"], OTHER_KEY)

    def test_a_store_we_do_not_understand_is_left_alone(self) -> None:
        self.agent.mkdir(parents=True, exist_ok=True)
        self.store.write_text("[not an object]", encoding="utf-8")

        code, _, err = self.run_auth("--store", "deepseek", checker=FakeChecker(), stdin=KEY)

        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(self.store.read_text(encoding="utf-8"), "[not an object]")

    def test_the_key_can_come_from_a_named_variable(self) -> None:
        with patch.dict(os.environ, {"MY_PROVIDER_KEY": KEY}):
            code, _, err = self.run_auth("--store", "deepseek", "--from-env", "MY_PROVIDER_KEY", checker=FakeChecker())

        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8"))["deepseek"]["key"], KEY)

    def test_a_variable_that_is_not_set_is_named_in_the_error(self) -> None:
        code, _, err = self.run_auth("--store", "deepseek", "--from-env", "NOPE_NOT_SET", checker=FakeChecker())

        self.assertEqual(code, 2)
        self.assertIn("NOPE_NOT_SET is not set", err)

    def test_the_key_can_come_from_a_file(self) -> None:
        path = self.root / "key.env"
        path.write_text(f"# comment\nDEEPSEEK_API_KEY={KEY}\n", encoding="utf-8")

        code, _, err = self.run_auth("--store", "deepseek", "--from-file", str(path), checker=FakeChecker())

        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8"))["deepseek"]["key"], KEY)

    def test_a_name_picks_one_key_out_of_a_file_with_several(self) -> None:
        path = self.root / "keys.env"
        path.write_text(f"ANTHROPIC_API_KEY={OTHER_KEY}\nDEEPSEEK_API_KEY={KEY}\n", encoding="utf-8")

        code, _, err = self.run_auth(
            "--store", "deepseek", "--from-file", str(path), "--from-var", "DEEPSEEK_API_KEY", checker=FakeChecker()
        )

        self.assertEqual(code, 0, err)
        written = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(written["deepseek"]["key"], KEY)

    def test_a_missing_file_is_named_in_the_error(self) -> None:
        code, _, err = self.run_auth("--store", "deepseek", "--from-file", str(self.root / "нет"), checker=FakeChecker())

        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_a_terminal_without_a_source_is_told_where_a_key_may_come_from(self) -> None:
        terminal = MagicMock()
        terminal.isatty.return_value = True
        err = io.StringIO()
        with patch("commahum.auth.sys.stdin", terminal), patch("commahum.auth.check", FakeChecker()):
            with contextlib.redirect_stderr(err):
                code = main(["auth", "--store", "deepseek"])

        self.assertEqual(code, 2)
        self.assertIn("not a place for a credential", err.getvalue())
        self.assertIn("stdin", err.getvalue())


class LoginTests(unittest.TestCase):
    def test_the_login_command_is_pi_itself_with_the_model_hints(self) -> None:
        class Arguments:
            provider = "deepseek"
            model = "deepseek/deepseek-flash"

        with patch("commahum.auth.pi_command", return_value=["node", "/vendor/pi/dist/bundle/cli.js"]):
            command = auth.login_argv(Arguments())

        self.assertEqual(
            command,
            ["node", "/vendor/pi/dist/bundle/cli.js", "--provider", "deepseek", "--model", "deepseek/deepseek-flash"],
        )

    def test_login_and_store_are_refused_together(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["auth", "--login", "--store", "deepseek"])

        self.assertEqual(code, 2)
        self.assertIn("one of them", err.getvalue())


if __name__ == "__main__":
    unittest.main()
