"""The command line.

The safe thing is the default: read the file, show what would change, write
nothing. `--write` is the opt-in, and even then the diff is printed, because the
diff is the thing a human is meant to look at.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
from pathlib import Path

from .codex_adapter import DEFAULT_MODEL
from .diff import render_diff
from .hermes_provider import provider_generator
from .rewrite import INSTRUCTIONS, rewrite_source

__all__ = ["main"]


def main(argv=None, *, generate=None) -> int:
    """Rewrite the comments of the given files, or of every `.nix` under a directory.

    Returns 0 when every file was read and rewritten, 1 when a file could not be
    handled. Comments that were deliberately skipped or that the model failed to
    rewrite are reported, but they are not errors.
    """
    parser = _build_parser()
    arguments = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if generate is None:
        generate = _generator_for(arguments)

    failed = False
    totals = {"rewritten": 0, "skipped": 0, "kept": 0, "files": 0, "requests": 0}
    for path in _collect(arguments.paths):
        if not path.is_file():
            print(f"{path}: no such file", file=sys.stderr)
            failed = True
            continue
        source = path.read_bytes()
        try:
            result = rewrite_source(source, generate)
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the rest
            print(f"{path}: {type(error).__name__}: {error}", file=sys.stderr)
            failed = True
            continue

        totals["files"] += 1
        totals["rewritten"] += len(result.applied)
        totals["skipped"] += len(result.skipped)
        totals["kept"] += len(result.unrewritten)
        totals["requests"] += result.requests

        diff = render_diff(source, result.rewritten, _display_name(path))
        if diff:
            print(diff, end="" if diff.endswith("\n") else "\n")
        if arguments.write and result.rewritten != source:
            _write_atomically(path, result.rewritten)

        print(
            f"{path}: {len(result.applied)} rewritten, "
            f"{len(result.skipped)} skipped, {len(result.unrewritten)} kept",
            file=sys.stderr,
        )
        for number, reason in result.unrewritten:
            print(f"{path}: comment {number} kept: {reason}", file=sys.stderr)

    print(
        f"{totals['files']} files, {totals['rewritten']} comments rewritten, "
        f"{totals['skipped']} skipped, {totals['kept']} kept, "
        f"{totals['requests']} requests",
        file=sys.stderr,
    )
    return 1 if failed else 0


def codex_generator(model: str = DEFAULT_MODEL):
    """A generator that sends each payload to the Codex subscription endpoint."""
    from .codex_adapter import CodexSubscriptionClient, HermesTokenStore

    client = CodexSubscriptionClient(token_store=HermesTokenStore(), model=model)

    def generate(prompt: str) -> str:
        return client.complete(prompt, instructions=INSTRUCTIONS)

    return generate


def _generator_for(arguments):
    """The Codex subscription by default; any vendored provider with ``--provider``."""
    if arguments.provider:
        return provider_generator(arguments.provider, arguments.model)
    return codex_generator(arguments.model or DEFAULT_MODEL)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commahum",
        description=(
            "Rewrite the comments of Nix files and show the result as a diff. "
            "Nothing is written without --write."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="files to rewrite, or directories to walk for *.nix",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="apply the rewrites to the files instead of only showing the diff",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "model to ask; without it the Codex default "
            f"({DEFAULT_MODEL}) or the provider's own default is used"
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "ask a provider from the vendored Hermes layer instead of the Codex "
            "subscription, e.g. deepseek, openrouter, nous"
        ),
    )
    return parser


def _collect(paths) -> list[Path]:
    files: list[Path] = []
    for argument in paths:
        path = Path(argument)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.nix")))
        else:
            files.append(path)
    return files


def _display_name(path: Path) -> str:
    """A short name for the diff headers: relative to the working directory if it can be."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path).lstrip("/")


def _write_atomically(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` in one step, keeping its mode.

    A config file caught halfway through a write is worse than one that was not
    rewritten at all, so the new bytes go to a temporary file first and are moved
    into place with a single ``os.replace``. A reader sees either the old file or
    the new one, never a truncated one.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(handle, mode)
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
