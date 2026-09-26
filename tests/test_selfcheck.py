"""`--quiet` must silence the diff, not just the per-file summary line.

A smoke run over a config tree prints one diff per file; without this the flag
looks like it worked (the summary still appears) while the terminal fills up.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SELFCHECK = REPO / "examples" / "selfcheck.py"

SOURCE = """{ ... }:
# Комментарий, который будет переписан.
{
  # Раздел.
  networking.hostName = "deep-thought";  # и ещё один, в конце строки
}
"""


class SelfcheckOutputTests(unittest.TestCase):
    def run_selfcheck(self, path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SELFCHECK), str(path), *extra],
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )

    def test_quiet_prints_no_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.nix"
            path.write_text(SOURCE, encoding="utf-8")
            result = self.run_selfcheck(path, "--quiet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("1 files", result.stderr)

    def test_without_quiet_the_diff_is_shown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.nix"
            path.write_text(SOURCE, encoding="utf-8")
            result = self.run_selfcheck(path)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("@@", result.stdout)
        self.assertIn("-# Комментарий", result.stdout)
        self.assertIn("+# КОММЕНТАРИЙ", result.stdout)
        self.assertIn("0 invariant violations", result.stderr)


if __name__ == "__main__":
    unittest.main()
