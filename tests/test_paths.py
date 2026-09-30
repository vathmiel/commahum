"""Overlapping selections must not cause duplicate model requests or writes."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commahum.paths import collect


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.child = self.root / "modules"
        self.child.mkdir()
        self.first = self.root / "a.nix"
        self.second = self.child / "b.nix"
        self.first.write_bytes(b"{}\n")
        self.second.write_bytes(b"{}\n")

    def test_overlapping_directories_and_explicit_files_are_collected_once(self) -> None:
        self.assertEqual(
            collect([self.root, self.child, self.first, self.root]),
            [self.first, self.second],
        )

    def test_first_occurrence_controls_order_and_display_path(self) -> None:
        self.assertEqual(collect([self.second, self.root]), [self.second, self.first])

    def test_relative_absolute_and_parent_directory_aliases_are_duplicates(self) -> None:
        with patch("os.getcwd", return_value=str(self.root)):
            self.assertEqual(collect(["a.nix", self.first, "modules/../a.nix"]), [Path("a.nix")])

    def test_symbolic_links_do_not_hide_the_regular_write_target(self) -> None:
        link = self.root / "link.nix"
        link.symlink_to(self.first)

        self.assertEqual(collect([link, self.first]), [link, self.first])

    def test_missing_paths_still_reach_the_caller_for_diagnostics(self) -> None:
        missing = self.root / "missing.nix"

        self.assertEqual(collect([missing, missing]), [missing])

    def test_aliases_of_a_directory_do_not_repeat_its_files(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.child, target_is_directory=True)

        self.assertEqual(collect([self.child, alias]), [self.second])


if __name__ == "__main__":
    unittest.main()
