"""The vendored tree is loaded fail-closed: a name it owns comes from it or not at all.

Without that, an installed Hermes in the same environment can serve a request
while the tree looks healthy, and a module missing from the tree shows up as a
provider that quietly lost its default model.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from commahum.hermes_tree import ENV_VAR, tree_root, vendored_roots

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "vendor" / "hermes"

DRIVER = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path

    tree, elsewhere = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path.insert(0, str(elsewhere))
    from commahum.hermes_tree import activate

    activate(tree)
    outcome = {}
    try:
        import fakepkg.present
        outcome["present"] = fakepkg.present.MARK
    except Exception as error:
        outcome["present"] = f"{type(error).__name__}: {error}"
    try:
        import fakepkg.absent
        outcome["absent"] = "imported"
    except ModuleNotFoundError as error:
        outcome["absent"] = str(error)
    outcome["resolved_from"] = Path(fakepkg.present.__file__).parent.parent.name
    print(json.dumps(outcome))
    """
)


class TreeRootTests(unittest.TestCase):
    def test_default_root_is_the_repository_tree(self) -> None:
        self.assertEqual(tree_root(), VENDOR)

    def test_the_environment_can_point_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get(ENV_VAR)
            os.environ[ENV_VAR] = directory
            try:
                self.assertEqual(tree_root(), Path(directory).resolve())
            finally:
                if previous is None:
                    os.environ.pop(ENV_VAR, None)
                else:
                    os.environ[ENV_VAR] = previous

    def test_roots_name_what_the_tree_owns(self) -> None:
        roots = vendored_roots(VENDOR)
        for name in ("agent", "hermes_cli", "hermes_platform", "pm", "providers", "tools"):
            with self.subTest(root=name):
                self.assertIn(name, roots)
        self.assertIn("hermes_constants", roots)
        # `plugins/` is deliberately absent: provider profiles are loaded by file
        # path, not imported by module name, so the finder has no say over them.
        self.assertNotIn("plugins", roots)

    def test_a_missing_tree_is_an_error_not_an_empty_set(self) -> None:
        with self.assertRaises(FileNotFoundError):
            vendored_roots(Path("/nonexistent/vendor/hermes"))


class FailClosedFinderTests(unittest.TestCase):
    """A synthetic tree, so the property is tested without the real one in the way."""

    def build_trees(self, directory: Path) -> tuple[Path, Path]:
        tree = directory / "tree"
        (tree / "fakepkg").mkdir(parents=True)
        (tree / "fakepkg" / "__init__.py").write_text("", encoding="utf-8")
        (tree / "fakepkg" / "present.py").write_text('MARK = "from the tree"\n', encoding="utf-8")

        elsewhere = directory / "elsewhere"
        (elsewhere / "fakepkg").mkdir(parents=True)
        (elsewhere / "fakepkg" / "__init__.py").write_text("", encoding="utf-8")
        (elsewhere / "fakepkg" / "present.py").write_text('MARK = "from elsewhere"\n', encoding="utf-8")
        (elsewhere / "fakepkg" / "absent.py").write_text('MARK = "shadow"\n', encoding="utf-8")
        return tree, elsewhere

    def test_a_name_the_tree_owns_never_comes_from_outside_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tree, elsewhere = self.build_trees(Path(directory))
            script = Path(directory) / "driver.py"
            script.write_text(DRIVER, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), str(tree), str(elsewhere)],
                capture_output=True,
                text=True,
                cwd=str(REPO),
                env=dict(os.environ, PYTHONPATH=str(REPO / "src")),
            )

        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        import json

        outcome = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(outcome["present"], "from the tree")
        self.assertEqual(outcome["resolved_from"], "tree")
        # `fakepkg.absent` exists in the shadow directory and must still be refused.
        self.assertIn("absent from the vendored tree", outcome["absent"])


if __name__ == "__main__":
    unittest.main()
