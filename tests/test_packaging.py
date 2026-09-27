"""The install paths: what `pip install commahum` has to bring with it.

Everything the pipeline runs is vendored, and none of it is this project's own
Python: the humanizer-ru skill (its rules and its scanner) and pi (its bundle and
the node_modules the bundle imports). A checkout finds those trees by walking up
from this package to the repository's `vendor/`. An installed wheel has no
repository above it — site-packages is as high as the walk goes — so the trees
have to travel *inside* the package, as `commahum/_vendor/`, and the walk has to
fall back to them there.

This module holds the install paths to that promise, cheapest first:

* a package-shaped directory — `commahum/` with `_vendor/` inside it and no
  repository above it — resolves the skill, the ban table and the pi entry;
* the wheel carries both trees, `node_modules` included, under
  `commahum/_vendor/`, byte for byte what the vendored manifests pin;
* the sdist carries them in the checkout layout, and a wheel built *from the
  sdist* carries them again: an sdist that cannot produce a complete wheel is a
  broken install path even when the wheel target is right;
* a wheel installed into a virtualenv of its own, outside this repository, answers
  `commahum check`, reads its rules from the installed skill and runs the
  installed pi.

Building uses the backend `pyproject.toml` names — `pip install hatchling` is the
one tool these tests need that the package itself does not; the wheel install takes
its dependencies from the index, the way `pip install commahum` would.
"""

from __future__ import annotations

import atexit
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
SKILL = VENDOR / "humanizer-ru"
PI = VENDOR / "pi"

SKILL_MANIFEST = json.loads((SKILL / "MANIFEST.json").read_text(encoding="utf-8"))
PI_VERSION = json.loads((PI / "package.json").read_text(encoding="utf-8"))["version"]

#: Where the vendored trees live in an installed wheel: inside the package.
PACKAGED_VENDOR = "commahum/_vendor"

#: What `python -c` prints for every layout that is asked the same four questions:
#: where the package is, what the skill says, whether the ban table is readable,
#: and which pi entry the package would run.
DISCOVERY = r"""
import json
from pathlib import Path

import commahum
from commahum import bans, skill
from commahum.pi_runner import pi_command, vendored_entry

found = skill.vendored_skill()
entry = vendored_entry()
print(json.dumps({
    "module": commahum.__file__,
    "skill": str(found.directory),
    "name": found.name,
    "invocation": found.invocation,
    "catalog_bytes": skill.catalog(found).stat().st_size,
    "markers": str(bans.markers_path()),
    "markers_present": bans.markers_path().is_file(),
    "bans": len(bans.hard_bans()),
    "entry": str(entry) if entry else None,
    "command": pi_command(),
}))
"""

#: The comment `check` is run on: valid Nix, one comment that the stand-in
#: rewriter will uppercase, so a pass means the invariant held on real parsing.
SAMPLE = """{ pkgs, ... }:
{
  # Комментарий, который трогать можно.
  environment.systemPackages = [ pkgs.git ];
}
"""


# --------------------------------------------------------------------------- #
# Building, once per (target, source) for the whole run.
# --------------------------------------------------------------------------- #

_SCRATCH: tempfile.TemporaryDirectory | None = None
_CACHE: dict[tuple[str, str], Path] = {}
_NUMBER = itertools.count(1)


def scratch() -> Path:
    """A directory of this run's own, cleaned up when the process exits."""
    global _SCRATCH
    if _SCRATCH is None:
        _SCRATCH = tempfile.TemporaryDirectory(prefix="commahum-packaging-")
        atexit.register(_SCRATCH.cleanup)
    return Path(_SCRATCH.name)


def build(target: str, *, source: Path = ROOT) -> Path:
    """One distribution, built the way the backend `pyproject.toml` names."""
    key = (target, str(source))
    if key in _CACHE:
        return _CACHE[key]
    into = scratch() / f"build-{next(_NUMBER)}"
    into.mkdir()
    done = subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", target, "-d", str(into)],
        cwd=source,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise AssertionError(
            f"hatchling build -t {target} failed in {source}; hatchling is the build "
            f"backend pyproject.toml names (`pip install hatchling`):\n"
            f"{done.stdout}\n{done.stderr}"
        )
    artifacts = list(into.iterdir())
    if len(artifacts) != 1:
        raise AssertionError(f"expected one {target} artifact in {into}, found {artifacts}")
    _CACHE[key] = artifacts[0]
    return artifacts[0]


def unpacked(sdist: Path) -> Path:
    """The sdist, extracted; returns the directory a builder would be run in."""
    into = scratch() / f"unpacked-{next(_NUMBER)}"
    with tarfile.open(sdist) as archive:
        archive.extractall(into, filter="data")
    roots = [entry for entry in into.iterdir() if entry.is_dir()]
    if len(roots) != 1:
        raise AssertionError(f"expected one root in {sdist}, found {roots}")
    return roots[0]


def wheel_names(wheel: Path) -> set[str]:
    """Every file the wheel carries, as paths inside site-packages."""
    with zipfile.ZipFile(wheel) as archive:
        return {name for name in archive.namelist() if not name.endswith("/")}


def wheel_hashes(wheel: Path) -> dict[str, str]:
    """sha256 of every file in the wheel, as paths inside site-packages."""
    with zipfile.ZipFile(wheel) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if not name.endswith("/")
        }


def tree_files(directory: Path) -> set[str]:
    """Every file under ``directory``, as paths relative to it.

    Bytecode caches are not vendored content — the pin test refuses them in the
    tree — so they are left out of what a distribution must carry.
    """
    return {
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def run_python(program: str, *, path: Path, cwd: Path) -> subprocess.CompletedProcess:
    """A python whose imports resolve the way an installed one's do."""
    environment = {**os.environ, "PYTHONPATH": str(path)}
    environment.pop("PYTHONSTARTUP", None)
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
    )


def outside_repository() -> dict:
    """The environment a plain `commahum` user has: nothing of this checkout on it."""
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP"):
        environment.pop(name, None)
    return environment


# --------------------------------------------------------------------------- #
# A package-shaped layout: the packaged tree, no repository above it.
# --------------------------------------------------------------------------- #


class PackagedLayoutTests(unittest.TestCase):
    """`commahum/_vendor/` is what the walks fall back to, so it has to answer."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="commahum-packaged-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.layout = Path(cls.temporary.name) / "site-packages"
        cls.build_layout(cls.layout)

    @classmethod
    def build_layout(cls, layout: Path) -> None:
        """A site-packages-shaped directory: the package and `_vendor/` only."""
        shutil.copytree(ROOT / "src" / "commahum", layout / "commahum")
        vendor = layout / "commahum" / "_vendor"
        for relative in (
            "humanizer-ru/SKILL.md",
            "humanizer-ru/edit-log.md",
            "humanizer-ru/references/review.md",
            "humanizer-ru/references/catalog.md",
            "humanizer-ru/scripts/humanizer_metrics/markers.py",
            "pi/dist/bundle/cli.js",
        ):
            destination = vendor / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(VENDOR / relative, destination)

    def discovery(self) -> dict:
        done = run_python(DISCOVERY, path=self.layout, cwd=self.layout)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def packaged(self, relative: str) -> Path:
        """Where a file of the vendored trees sits inside the laid-out package."""
        return self.layout / "commahum" / "_vendor" / relative

    def test_the_package_imported_is_the_one_laid_out(self) -> None:
        found = Path(self.discovery()["module"]).resolve()

        self.assertEqual(found, (self.layout / "commahum" / "__init__.py").resolve())

    def test_the_rules_are_found_in_the_packaged_tree(self) -> None:
        found = self.discovery()

        self.assertEqual(found["name"], "humanizer-ru")
        self.assertEqual(found["invocation"], "/skill:humanizer-ru")
        self.assertTrue(
            Path(found["skill"]).is_relative_to(self.layout),
            f"{found['skill']} is not inside {self.layout}",
        )
        self.assertEqual(Path(found["skill"]).name, "humanizer-ru")
        self.assertGreater(found["catalog_bytes"], 40_000)

    def test_the_ban_table_is_read_from_the_packaged_tree(self) -> None:
        found = self.discovery()
        markers = self.packaged("humanizer-ru/scripts/humanizer_metrics/markers.py")

        self.assertEqual(found["markers"], str(markers))
        self.assertTrue(found["markers_present"])
        self.assertGreaterEqual(found["bans"], 21)

    def test_the_pi_entry_is_the_packaged_one(self) -> None:
        found = self.discovery()

        self.assertEqual(found["entry"], str(self.packaged("pi/dist/bundle/cli.js")))
        self.assertEqual(found["command"][1], found["entry"])
        self.assertEqual(Path(found["command"][0]).name, "node")

    def test_a_layout_with_neither_tree_says_so(self) -> None:
        """No packaged copy and no repository above it is the failure to name."""
        empty = Path(tempfile.mkdtemp(prefix="commahum-empty-", dir=scratch()))
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        shutil.copytree(ROOT / "src" / "commahum", empty / "commahum")

        done = run_python(DISCOVERY, path=empty, cwd=empty)

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("no vendored skill", done.stderr)


# --------------------------------------------------------------------------- #
# The two distributions, by what they carry.
# --------------------------------------------------------------------------- #


class WheelTests(unittest.TestCase):
    """What `pip install <wheel>` puts in site-packages."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.wheel = build("wheel")
        cls.names = wheel_names(cls.wheel)

    def vendored(self, relative: str) -> set[str]:
        """Every file the wheel carries under `commahum/_vendor/<relative>`."""
        prefix = f"{PACKAGED_VENDOR}/{relative}/"
        return {name[len(prefix) :] for name in self.names if name.startswith(prefix)}

    def test_the_skill_travels_inside_the_package(self) -> None:
        for relative in (
            "SKILL.md",
            "edit-log.md",
            "references/review.md",
            "references/catalog.md",
            "scripts/scan.py",
        ):
            with self.subTest(file=relative):
                self.assertIn(f"{PACKAGED_VENDOR}/humanizer-ru/{relative}", self.names)

    def test_pi_travels_inside_the_package_node_modules_included(self) -> None:
        self.assertIn(f"{PACKAGED_VENDOR}/pi/dist/bundle/cli.js", self.names)
        self.assertIn(f"{PACKAGED_VENDOR}/pi/package.json", self.names)
        self.assertIn(
            f"{PACKAGED_VENDOR}/pi/node_modules/@earendil-works/chord/package.json", self.names
        )
        self.assertIn(f"{PACKAGED_VENDOR}/pi/node_modules/typebox/package.json", self.names)
        self.assertGreater(
            len(self.vendored("pi/node_modules")),
            1_000,
            "the bundled pi imports its node_modules at runtime; a wheel without them "
            "answers `commahum rewrite` with a node module error",
        )

    def test_the_wheel_carries_the_whole_of_both_trees_and_nothing_else(self) -> None:
        self.assertEqual(self.vendored("humanizer-ru"), tree_files(SKILL))
        self.assertEqual(self.vendored("pi"), tree_files(PI))

    def test_the_vendored_bytes_are_the_pinned_ones(self) -> None:
        """The copy travels verbatim: no re-encoding, no line ending repair."""
        digests = wheel_hashes(self.wheel)

        for relative, digest in SKILL_MANIFEST["tree"]["sha256"].items():
            with self.subTest(file=relative):
                name = f"{PACKAGED_VENDOR}/humanizer-ru/{relative}"
                self.assertIn(name, digests)
                self.assertEqual(digests[name], digest)

    def test_the_wheel_keeps_the_vendored_trees_inside_the_package(self) -> None:
        """`vendor/` at the top of site-packages would be nobody's to own."""
        loose = sorted(name for name in self.names if name.startswith("vendor/"))

        self.assertEqual(loose, [])

    def test_the_package_itself_is_still_the_wheels_only_python(self) -> None:
        self.assertIn("commahum/cli.py", self.names)
        self.assertIn("commahum/skill.py", self.names)

    def test_the_declared_install_contract_survives(self) -> None:
        """`pip install commahum[style]` and the console script are metadata too."""
        metadata_name = next(name for name in self.names if name.endswith(".dist-info/METADATA"))
        points_name = next(
            name for name in self.names if name.endswith(".dist-info/entry_points.txt")
        )
        with zipfile.ZipFile(self.wheel) as archive:
            metadata = archive.read(metadata_name).decode("utf-8")
            points = archive.read(points_name).decode("utf-8")

        required = " ".join(
            line
            for line in metadata.splitlines()
            if line.startswith("Requires-Dist:") and "extra ==" not in line
        )
        optional = " ".join(line for line in metadata.splitlines() if "extra ==" in line)

        for dependency in ("tree-sitter==0.25.2", "tree-sitter-nix==0.1.0"):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, required)
        self.assertIn("Provides-Extra: style", metadata)
        for dependency in ("razdel", "pymorphy3", "pymorphy3-dicts-ru"):
            with self.subTest(extra=dependency):
                self.assertIn(dependency, optional)
        self.assertIn("commahum = commahum.cli:main", points)


class SdistTests(unittest.TestCase):
    """What `pip install <sdist>` builds from — the same trees, or no wheel."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sdist = build("sdist")
        with tarfile.open(cls.sdist) as archive:
            cls.names = {member.name for member in archive.getmembers() if member.isfile()}

    @property
    def root(self) -> str:
        return f"commahum-{self.sdist.name.split('-', 1)[1][: -len('.tar.gz')]}"

    def vendored(self, relative: str) -> set[str]:
        prefix = f"{self.root}/vendor/{relative}/"
        return {name[len(prefix) :] for name in self.names if name.startswith(prefix)}

    def test_the_sdist_brings_what_a_build_needs(self) -> None:
        for relative in ("pyproject.toml", "README.md", "LICENSE", "src/commahum/cli.py"):
            with self.subTest(file=relative):
                self.assertIn(f"{self.root}/{relative}", self.names)

    def test_the_sdist_carries_the_vendored_trees_in_their_checkout_layout(self) -> None:
        self.assertEqual(self.vendored("humanizer-ru"), tree_files(SKILL))
        self.assertEqual(self.vendored("pi"), tree_files(PI))

    def test_a_wheel_built_from_the_sdist_carries_the_same_trees(self) -> None:
        """The path `pip install commahum-0.1.0.tar.gz` takes, all the way through."""
        from_sdist = build("wheel", source=unpacked(self.sdist))
        carried = wheel_names(from_sdist)
        prefix = f"{PACKAGED_VENDOR}/"

        self.assertEqual(
            {name for name in carried if name.startswith(prefix)},
            {name for name in wheel_names(build("wheel")) if name.startswith(prefix)},
        )


# --------------------------------------------------------------------------- #
# The installed wheel, in a virtualenv with nothing of this checkout reachable.
# --------------------------------------------------------------------------- #


class InstalledWheelTests(unittest.TestCase):
    """The claim that matters: `pip install` alone, outside the repository."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="commahum-installed-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        cls.work = directory / "work"
        cls.work.mkdir()

        cls.venv = directory / "venv"
        venv.EnvBuilder(with_pip=True).create(cls.venv)
        cls.python = cls.venv / "bin" / "python"
        cls.console = cls.venv / "bin" / "commahum"

        cls.wheel = build("wheel")
        install = subprocess.run(
            [
                str(cls.python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                str(cls.wheel),
            ],
            cwd=cls.work,
            env=outside_repository(),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if install.returncode != 0:
            raise AssertionError(
                f"installing {cls.wheel} into {cls.venv} failed:\n"
                f"{install.stdout}\n{install.stderr}"
            )
        cls.site_packages = Path(
            subprocess.run(
                [str(cls.python), "-c", "import site; print(site.getsitepackages()[0])"],
                cwd=cls.work,
                env=outside_repository(),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )

    def discovery(self) -> dict:
        done = subprocess.run(
            [str(self.python), "-c", DISCOVERY],
            cwd=self.work,
            env=outside_repository(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_installed_package_is_the_wheel_and_not_this_checkout(self) -> None:
        found = self.discovery()

        self.assertTrue(
            Path(found["module"]).is_relative_to(self.site_packages), found["module"]
        )
        self.assertFalse(
            Path(found["module"]).is_relative_to(ROOT), f"{found['module']} is this checkout"
        )

    def test_the_rules_are_read_from_the_installed_package(self) -> None:
        found = self.discovery()

        self.assertEqual(found["name"], "humanizer-ru")
        self.assertEqual(found["invocation"], "/skill:humanizer-ru")
        self.assertTrue(Path(found["skill"]).is_relative_to(self.site_packages), found["skill"])
        self.assertEqual(Path(found["skill"]).name, "humanizer-ru")
        self.assertGreater(found["catalog_bytes"], 40_000)
        self.assertEqual(
            Path(found["markers"]),
            Path(found["skill"]) / "scripts" / "humanizer_metrics" / "markers.py",
        )
        self.assertTrue(found["markers_present"])
        self.assertGreaterEqual(found["bans"], 21)

    def test_the_installed_pi_entry_is_the_vendored_copy(self) -> None:
        found = self.discovery()
        entry = Path(found["entry"])
        vendor = Path(found["skill"]).parent

        self.assertTrue(entry.is_relative_to(self.site_packages), found["entry"])
        self.assertEqual(entry, vendor / "pi/dist/bundle/cli.js")
        self.assertEqual(found["command"][1], found["entry"])
        self.assertTrue(
            (vendor / "pi/node_modules/@earendil-works/chord/package.json").is_file(),
            "the installed pi is missing the node_modules its bundle imports",
        )

    def test_the_installed_pi_runs(self) -> None:
        """Not just present: node loads the bundle and its modules, and answers."""
        entry = Path(self.discovery()["entry"])
        done = subprocess.run(
            [shutil.which("node") or "node", str(entry), "--version"],
            cwd=self.work,
            env={**outside_repository(), "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"},
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), PI_VERSION)

    def test_check_runs_on_a_file_with_nothing_of_this_checkout_reachable(self) -> None:
        """The command the suite trusts for the comment-only invariant, installed."""
        source = self.work / "sample.nix"
        source.write_text(SAMPLE, encoding="utf-8")

        done = subprocess.run(
            [str(self.console), "check", str(source)],
            cwd=self.work,
            env=outside_repository(),
            capture_output=True,
            text=True,
        )

        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("0 invariant violations", done.stderr)
        self.assertIn("1 files", done.stderr)
        self.assertNotEqual(done.stdout, "", "check printed no diff for a comment it rewrote")


if __name__ == "__main__":
    unittest.main()
