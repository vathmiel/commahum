"""`check` proves the comment-only invariant with a stand-in model, no network.

It is the one command that needs neither pi nor a key, so it is the one to run over
a real config tree before trusting anything else — and the one whose output has to
stay honest: the diff on stdout, the per-file line and the summary on stderr, and
`--quiet` that removes the diff rather than merely hiding the per-file line.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from commahum.cli import main

SOURCE = """{ ... }:
# Комментарий, который будет переписан.
{
  # Раздел.
  networking.hostName = "deep-thought";  # и ещё один, в конце строки
}
"""


def run_check(*argv: str) -> tuple[int, str, str]:
    """Run the command the way the shell would, and give back what it printed."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = main(["check", *argv])
    return status, out.getvalue(), err.getvalue()


class CheckCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_quiet_prints_no_diff(self) -> None:
        path = self.write("sample.nix", SOURCE)

        status, out, err = run_check(str(path), "--quiet")

        self.assertEqual(status, 0, err)
        self.assertEqual(out, "")
        self.assertIn("1 files", err)

    def test_without_quiet_the_diff_is_shown(self) -> None:
        path = self.write("sample.nix", SOURCE)

        status, out, err = run_check(str(path))

        self.assertEqual(status, 0, err)
        self.assertIn("@@", out)
        self.assertIn("-# Комментарий", out)
        self.assertIn("+# КОММЕНТАРИЙ", out)
        self.assertIn("0 invariant violations", err)

    def test_a_directory_is_walked_for_nix_files(self) -> None:
        self.write("a.nix", "# первый\n{}\n")
        self.write("b.nix", "# второй\n{}\n")
        self.write("notes.txt", "# не nix\n")

        status, _, err = run_check(str(self.root), "--quiet")

        self.assertEqual(status, 0, err)
        self.assertIn("2 files", err)

    def test_a_file_that_cannot_be_read_is_a_violation_and_not_a_crash(self) -> None:
        """One bad file must not take the run's summary down with it."""
        missing = self.root / "нет-такого.nix"

        status, _, err = run_check(str(missing), "--quiet")

        self.assertEqual(status, 1)
        self.assertIn("ERROR", err)
        self.assertIn("1 invariant violations", err)


if __name__ == "__main__":
    unittest.main()
