"""The `report` command: rewrite, then grade the result with the skill's own scanner.

The skill ships the deterministic half of its audit: `scripts/scan.py` prints a
cleanliness score, names the hard bans it found, and, with `--before`, checks the
result against the original — including facts the rewrite invented. This command
runs the pipeline over the given files, writes the comment prose before and after
to temporary files, and lets that scanner do the judging, so compliance is a number
instead of a claim.

    commahum report --model deepseek/deepseek-flash /etc/nixos/modules

Only the prose the model was allowed to see is scanned: comment markers are
stripped, code never reaches the scanner. The scanner needs razdel and pymorphy3,
which is why it is an extra (`pip install -e '.[style]'`) and not a dependency of
the package: nothing here imports the skill's code.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from . import asking
from .paths import collect
from .rewrite import rewrite_source
from .selection import prose
from .skill import vendored_skill

__all__ = ["add_arguments", "prose", "run"]


def add_arguments(parser) -> None:
    parser.add_argument("paths", nargs="+", help="files to rewrite, or directories to walk for *.nix")
    asking.add_options(parser, catalog=True)
    parser.add_argument("--genre", default=None, help="pass a genre through to the scanner")
    parser.add_argument(
        "--keep",
        default=None,
        help="write the scanned before/after texts here instead of a temporary directory",
    )


def run(arguments, generate=None) -> int:
    skill = vendored_skill()
    if generate is None:
        generate = asking.generator(arguments, skill=skill)

    before_parts: list[str] = []
    after_parts: list[str] = []
    for path in collect(arguments.paths):
        source = path.read_bytes()
        result = rewrite_source(source, generate, invocation=skill.invocation)
        before_parts.append(f"# {path}\n{prose(source)}")
        after_parts.append(f"# {path}\n{prose(result.rewritten)}")
        print(
            f"{path}: {len(result.applied)} rewritten, "
            f"{len(result.skipped)} skipped, {len(result.unrewritten)} kept, "
            f"{result.requests} requests",
            file=sys.stderr,
        )

    scratch = arguments.keep
    with tempfile.TemporaryDirectory() if scratch is None else _kept(scratch) as directory:
        before_file = Path(directory) / "before.txt"
        after_file = Path(directory) / "after.txt"
        before_file.write_text("\n\n".join(before_parts), encoding="utf-8")
        after_file.write_text("\n\n".join(after_parts), encoding="utf-8")
        if scratch is not None:
            print(f"scanned: {after_file} against {before_file}", file=sys.stderr)
        # `-B`: the scanner imports the skill's own package, and bytecode caches
        # would land inside the vendored tree — where the pin tests would then
        # find files the upstream revision does not have.
        command = [
            sys.executable,
            "-B",
            str(skill.directory / "scripts" / "scan.py"),
            str(after_file),
            "--before",
            str(before_file),
        ]
        if arguments.genre:
            command += ["--genre", arguments.genre]
        scan = subprocess.run(command, text=True, capture_output=True)
    print(scan.stdout, end="")
    print(scan.stderr, end="", file=sys.stderr)
    print(f"scanner exit: {scan.returncode} (2 means the rewrite invented a fact)")
    return scan.returncode


class _kept:
    """A directory that is created and handed over, but not cleaned up."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def __enter__(self) -> str:
        self.path.mkdir(parents=True, exist_ok=True)
        return str(self.path)

    def __exit__(self, *_: object) -> bool:
        return False
