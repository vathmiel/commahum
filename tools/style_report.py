"""Measure what the rewriting did with humanizer-ru's own scanner.

The skill ships the deterministic half of its audit: `scan.py` prints a
cleanliness score, names the hard bans it found, and, with `--before`, checks the
result against the original — including facts the rewrite invented. This tool runs
the pipeline over the given files, writes the comment prose before and after to
temporary files, and lets that scanner do the judging, so compliance is a number
instead of a claim.

    .venv/bin/python tools/style_report.py --provider deepseek /etc/nixos/modules

Only the prose the model was allowed to see is scanned: comment markers are
stripped, code never reaches the scanner. The scanner needs razdel and pymorphy3,
which is why it is a tool and not part of the package.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commahum.cli import _generator_for, _collect  # noqa: E402
from commahum.rewrite import rewrite_source  # noqa: E402
from commahum.selection import select_candidates  # noqa: E402

_SCANNER = (
    Path(__file__).resolve().parents[1]
    / "vendor"
    / "humanizer-ru"
    / "scripts"
    / "scan.py"
)


def prose(source: bytes) -> str:
    """The prose the model sees, one comment per line."""
    return "\n".join(
        candidate.body.decode("utf-8", "surrogateescape").replace("\n", " ")
        for candidate in select_candidates(source).candidates
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("paths", nargs="+", help="files, or directories to walk for *.nix")
    parser.add_argument("--provider", default=None, help="provider from the vendored Hermes layer")
    parser.add_argument("--model", default=None, help="model to ask")
    parser.add_argument("--genre", default=None, help="pass a genre through to the scanner")
    parser.add_argument(
        "--keep",
        default=None,
        help="write the scanned before/after texts here instead of a temporary directory",
    )
    arguments = parser.parse_args(argv)

    generate = _generator_for(arguments)
    before_parts: list[str] = []
    after_parts: list[str] = []
    for path in _collect(arguments.paths):
        source = path.read_bytes()
        result = rewrite_source(source, generate)
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
        command = [sys.executable, str(_SCANNER), str(after_file), "--before", str(before_file)]
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


if __name__ == "__main__":
    raise SystemExit(main())
