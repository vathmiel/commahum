"""The vendored Hermes tree must stay importable on its own.

`vendor/hermes/` is a copy, not an install, so a provider integration added later
can quietly depend on a module nobody copied. `tools/closure_probe.py` finds and
repairs that; this test is the cheap guard that runs with the rest of the suite —
one subprocess, every AI-integration entry point, and a check that nothing was
loaded from an installed Hermes instead of the tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "vendor" / "hermes"

DRIVER = """
import importlib, json, sys
from pathlib import Path

sys.path.insert(0, {tools!r})
import closure_probe as probe
from commahum.hermes_tree import activate

activate()
failures = []
for name in probe.INTEGRATIONS:
    try:
        importlib.import_module(name)
    except Exception as error:
        failures.append(f"{{name}}: {{type(error).__name__}}: {{error}}")

roots = probe.vendored_roots(probe.VENDOR)
leaks = [
    f"{{name}}: {{module.__file__}}"
    for name, module in list(sys.modules.items())
    if (path := getattr(module, "__file__", None))
    and Path(path).parts
    and Path(path).parts[0] in roots
    and not Path(path).is_relative_to(probe.VENDOR)
]
print(json.dumps({{"failures": failures, "leaks": leaks}}))
"""


class VendoredTreeTests(unittest.TestCase):
    def test_tree_holds_the_hermes_packages(self) -> None:
        self.assertTrue(VENDOR.is_dir(), f"missing vendored tree at {VENDOR}")

        for package in ("agent", "hermes_cli", "hermes_platform", "pm", "providers", "tools"):
            with self.subTest(package=package):
                self.assertTrue((VENDOR / package / "__init__.py").is_file())

    def test_provider_profiles_are_present_and_register(self) -> None:
        """Profiles are scanned, not imported: without them a provider silently loses
        its default model and capabilities while every import still succeeds."""
        profiles = VENDOR / "plugins" / "model-providers"
        self.assertTrue(profiles.is_dir(), f"missing provider profiles at {profiles}")

        for name in ("deepseek", "openrouter", "nous", "anthropic", "openai-codex"):
            with self.subTest(provider=name):
                self.assertTrue((profiles / name / "__init__.py").is_file())
                self.assertTrue((profiles / name / "plugin.yaml").is_file())

        # Providers whose profile declares a static auxiliary default. Others
        # (openrouter, nous, openai-codex) resolve theirs from live discovery, so a
        # blank default_aux_model is correct for them — what matters is that the
        # profile itself registers instead of silently becoming None.
        program = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(REPO / 'tools')!r})\n"
            "from commahum.hermes_tree import activate\n"
            "activate()\n"
            "from providers import get_provider_profile\n"
            "found = {}\n"
            "for name in ('deepseek', 'openrouter', 'nous', 'anthropic', 'openai-codex'):\n"
            "    profile = get_provider_profile(name)\n"
            "    found[name] = {\n"
            "        'class': type(profile).__name__ if profile else None,\n"
            "        'default_aux_model': getattr(profile, 'default_aux_model', None) if profile else None,\n"
            "        'base_url': getattr(profile, 'base_url', None) if profile else None,\n"
            "        'module_file': getattr(sys.modules.get(type(profile).__module__), '__file__', None) if profile else None,\n"
            "    }\n"
            "print(json.dumps(found))\n"
        )
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, "-c", program],
                capture_output=True,
                text=True,
                cwd=str(REPO),
                env=dict(os.environ, HERMES_HOME=home, PYTHONDONTWRITEBYTECODE="1"),
            )

        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        found = json.loads(result.stdout.strip().splitlines()[-1])
        for name, fields in found.items():
            with self.subTest(provider=name):
                self.assertIsNotNone(fields["class"], f"{name} registered no profile")
                self.assertTrue(fields["base_url"], f"{name} profile has no base URL")

        self.assertEqual(found["deepseek"]["default_aux_model"], "deepseek-flash")
        self.assertEqual(found["anthropic"]["default_aux_model"], "claude-haiku-4-5-20251001")
        for name, fields in found.items():
            with self.subTest(provider=name):
                self.assertTrue(
                    fields["module_file"] and fields["module_file"].startswith(str(VENDOR)),
                    f"{name} resolved a profile from outside the tree: {fields['module_file']}",
                )

    def test_integration_surface_imports_from_the_tree_alone(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, "-c", DRIVER.format(tools=str(REPO / "tools"))],
                capture_output=True,
                text=True,
                cwd=str(REPO),
                env=dict(os.environ, HERMES_HOME=home, PYTHONDONTWRITEBYTECODE="1"),
            )

        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["failures"], [])
        self.assertEqual(payload["leaks"], [])


if __name__ == "__main__":
    unittest.main()
