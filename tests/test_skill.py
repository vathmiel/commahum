"""Guarantees about the skill the rules come from.

Three things are checked here and none of them needs a network or a model:

* the copy in this checkout is the pinned revision, byte for byte — the manifest
  carries the sha256 of every file taken from the tag's tarball, so drift between
  the vendor directory and the pin fails the suite;
* the skill says what a skill has to say — a name, a description, a licence — and
  the name is what requests are built around;
* anything that would leave a run without usable rules raises instead of
  returning an empty or default answer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commahum.skill import CATALOG, FLOW_READING, Skill, SkillError, vendored_path, vendored_skill


@contextlib.contextmanager
def temporary(text: str):
    """A `SKILL.md` in a directory of its own, for the refusal cases."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "SKILL.md"
        path.write_text(text, encoding="utf-8")
        yield path

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "humanizer-ru"
MANIFEST = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))

#: Files this project adds beside the skill's own: the licence text and the manifest.
OURS = {"LICENSE.humanizer-ru", "MANIFEST.json"}


class CopyTests(unittest.TestCase):
    """The copy is the pinned revision, and the pin is checkable."""

    def test_every_pinned_file_is_present_and_unchanged(self) -> None:
        for relative, digest in MANIFEST["tree"]["sha256"].items():
            path = VENDOR / relative
            with self.subTest(file=relative):
                self.assertTrue(path.is_file(), f"{relative} is missing from the copy")
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    digest,
                    f"{relative} no longer matches the pinned revision",
                )

    def test_the_copy_holds_nothing_the_pin_does_not(self) -> None:
        pinned = set(MANIFEST["tree"]["sha256"])
        found = {
            str(path.relative_to(VENDOR))
            for path in VENDOR.rglob("*")
            if path.is_file() and str(path.relative_to(VENDOR)) not in OURS
        }

        extra = found - pinned
        if any("__pycache__" in name or name.endswith(".pyc") for name in extra):
            self.fail(
                "bytecode caches are sitting in the vendored tree: "
                f"{sorted(extra)}. Something imported the skill's package without -B; "
                "The `report` command runs the scanner with -B for this reason."
            )
        self.assertEqual(found, pinned)

    def test_the_manifest_counts_what_it_maps(self) -> None:
        self.assertEqual(MANIFEST["tree"]["files"], len(MANIFEST["tree"]["sha256"]))

    def test_the_pin_names_a_revision_this_project_means(self) -> None:
        """A tag and a commit, because a tag can be moved and a commit cannot."""
        revision = MANIFEST["revision"]

        self.assertRegex(revision, r"^v\d+\.\d+\.\d+ \([0-9a-f]{40}\)$")
        self.assertTrue(MANIFEST["tarball_sha256"])
        self.assertIn("MIT", MANIFEST["license"])

    def test_the_repository_carries_no_other_skill(self) -> None:
        """What `skills_in_repository` claims is what the copy was made from."""
        self.assertEqual(MANIFEST["skills_in_repository"], [VENDOR.name])


class FrontmatterTests(unittest.TestCase):
    """The skill says what it is, and this project reads it rather than assuming."""

    def test_the_name_matches_the_directory_it_lives_in(self) -> None:
        skill = vendored_skill()

        self.assertEqual(skill.name, VENDOR.name)
        self.assertEqual(skill.invocation, f"/skill:{VENDOR.name}")

    def test_the_standard_fields_are_there(self) -> None:
        skill = vendored_skill()

        self.assertEqual(skill.license, "MIT")
        self.assertGreater(len(skill.description), 200)
        self.assertIn("Триггеры", skill.description)

    def test_the_invocation_is_a_slash_command_pi_understands(self) -> None:
        self.assertEqual(vendored_skill().invocation, "/skill:humanizer-ru")

    def test_the_opening_is_the_invocation_and_a_space(self) -> None:
        """The space is what makes pi find the skill: it reads the name up to it.

        With a newline there instead, the name it looks up is `humanizer-ru\\n…`,
        nothing matches, and pi passes the prompt through without a word — the
        request would carry no rules and nothing would say so.
        """
        skill = vendored_skill()

        self.assertEqual(skill.opening, f"{skill.invocation} ")
        self.assertTrue(skill.opening.endswith(" "))

    def test_no_frontmatter_is_refused(self) -> None:
        with temporary("name: x\n") as path, self.assertRaises(SkillError) as raised:
            _parse(path)

        self.assertIn("no frontmatter", str(raised.exception))

    def test_an_unclosed_frontmatter_is_refused(self) -> None:
        with temporary("---\nname: x\ndescription: y\n") as path, self.assertRaises(
            SkillError
        ) as raised:
            _parse(path)

        self.assertIn("never closed", str(raised.exception))

    def test_a_missing_name_is_refused(self) -> None:
        with temporary("---\ndescription: y\n---\n") as path, self.assertRaises(
            SkillError
        ) as raised:
            _parse(path)

        self.assertIn("no name", str(raised.exception))

    def test_a_name_that_is_not_a_slug_is_refused(self) -> None:
        """`/skill:Humanizer RU` is not a command anyone can call."""
        with temporary("---\nname: Humanizer RU\ndescription: y\n---\n") as path:
            with self.assertRaises(SkillError) as raised:
                _parse(path)

        self.assertIn("not a usable skill name", str(raised.exception))

    def test_a_description_is_required(self) -> None:
        """The description is what an agent sees before it loads anything."""
        with temporary("---\nname: x\n---\n") as path, self.assertRaises(SkillError) as raised:
            _parse(path)

        self.assertIn("no description", str(raised.exception))

    def test_a_line_that_is_not_a_pair_is_refused(self) -> None:
        with temporary("---\nname: x\ndescription: y\njust a line\n---\n") as path:
            with self.assertRaises(SkillError) as raised:
                _parse(path)

        self.assertIn("key: value", str(raised.exception))

    def test_quotes_around_a_value_are_not_part_of_it(self) -> None:
        with temporary('---\nname: x\ndescription: "y: z"\n---\n') as path:
            self.assertEqual(_parse(path).description, "y: z")


class MissingSkillTests(unittest.TestCase):
    """No skill means no run, not a run with default rules."""

    def test_a_checkout_without_the_skill_says_so(self) -> None:
        with patch("commahum.skill.VENDORED_SKILL", ("vendor", "not-a-skill")):
            with self.assertRaises(SkillError) as raised:
                vendored_skill()

        self.assertIn("no vendored skill", str(raised.exception))

    def test_a_companion_file_that_is_missing_is_an_error(self) -> None:
        skill = vendored_skill()

        with self.assertRaises(SkillError) as raised:
            skill.file("references/nowhere.md")

        self.assertIn("no references/nowhere.md", str(raised.exception))


class VendorIsolationTests(unittest.TestCase):
    def test_partial_packaged_tree_never_uses_an_ancestor_vendor(self) -> None:
        from commahum import skill as module

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            package = root / "site-packages" / "commahum"
            packaged_skill = package / "_vendor" / "humanizer-ru" / "SKILL.md"
            packaged_skill.parent.mkdir(parents=True)
            packaged_skill.write_text("---\nname: humanizer-ru\ndescription: Test\n---\n", encoding="utf-8")
            foreign = root / "vendor" / "humanizer-ru" / "scripts" / "humanizer_metrics" / "markers.py"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("foreign = True\n", encoding="utf-8")

            with patch.object(module, "__file__", str(package / "skill.py")):
                self.assertEqual(vendored_skill().directory, packaged_skill.parent)
                self.assertIsNone(vendored_path("humanizer-ru/scripts/humanizer_metrics/markers.py"))


class FlowTests(unittest.TestCase):
    """What the flow reads, and what this project therefore hands to pi."""

    def test_the_reading_is_the_two_files_the_skill_names_in_order(self) -> None:
        skill = vendored_skill()

        self.assertEqual(FLOW_READING, ("edit-log.md", "references/review.md"))
        for relative in FLOW_READING:
            with self.subTest(file=relative):
                self.assertTrue(skill.file(relative).is_file())

    def test_the_skill_itself_says_to_read_them(self) -> None:
        """The list is not this project's taste: the file it ships with says so."""
        body = vendored_skill().reading("SKILL.md")

        self.assertIn("[edit-log.md](edit-log.md)", body)
        self.assertIn("[references/review.md](references/review.md)", body)
        self.assertIn("[references/catalog.md](references/catalog.md)", body)

    def test_the_catalogue_is_the_pattern_list_and_is_reachable(self) -> None:
        skill = vendored_skill()
        path = skill.file(CATALOG)

        self.assertEqual(path.name, "catalog.md")
        self.assertEqual(path, skill.directory / CATALOG)
        self.assertGreater(path.stat().st_size, 40_000)

    def test_the_catalogue_declares_the_pattern_count_the_manifest_records(self) -> None:
        text = vendored_skill().reading(CATALOG)
        found = re.search(r"Каталог: (\d+) паттернов", text)

        self.assertIsNotNone(found, "the catalogue no longer states how many patterns it holds")
        assert found is not None
        declared = int(found.group(1))

        self.assertEqual(declared, MANIFEST["tree"]["catalog_patterns"])

    def test_the_register_the_pipeline_uses_is_one_the_skill_defines(self) -> None:
        """INSTRUCTIONS fixes the genre, so the genre has to be one the skill knows."""
        from commahum.rewrite import INSTRUCTIONS

        self.assertIn("документация", INSTRUCTIONS)
        self.assertIn("документация", vendored_skill().reading("SKILL.md"))


def _parse(path: Path) -> Skill:
    """Parse a `SKILL.md` through the same code the pipeline uses, cache and all."""
    from commahum import skill as module

    module._parse.cache_clear()
    try:
        return module._parse(path)
    finally:
        module._parse.cache_clear()


if __name__ == "__main__":
    unittest.main()
