"""The command line: one program, six commands.

    commahum rewrite <paths>   rewrite comments, show the diff, write only with --write
    commahum report  <paths>   rewrite, then grade the result with the skill's own scanner
    commahum measure <paths>   run a corpus and print the numbers, saving or comparing a baseline
    commahum probe             ask pi for words that exist only in the files it was given
    commahum check   <paths>   verify the invariant with a stand-in model — no model, no writes
    commahum auth              what pi is ready for, where the credential came from, and storing one

A bare `commahum <paths>` means `rewrite`, because that is the safe default: read
the file, show what would change, write nothing. `--write` is the opt-in, and even
then the diff is printed, because the diff is the thing a human is meant to look at.

Stopping is safe too. Ctrl+C, a termination signal or a reader that closed the pipe
end the run between files; each file is either rewritten whole or left alone; the
summary says how far the run got.

A file that changes under the run is not clobbered either. It is re-read
immediately before the write, and one that no longer matches what the rewrite was
computed from is reported and left alone, while the run goes on to the rest.

The rules are the vendored skill's, and every request loads it. A skill that is
missing or unreadable stops the run before any file is touched: rewriting comments
with no rules to follow would be inventing them.
"""

from __future__ import annotations

import argparse
import os
import signal
import stat
import sys
import tempfile

from . import asking, auth, measure, probe, report, selfcheck
from .diff import render_diff
from .interrupts import PIPE_STATUS, restore_interrupts, silence_stdout, trap_interrupts
from .paths import collect, display_name
from .rewrite import rewrite_source
from .skill import SkillError, vendored_skill

__all__ = ["COMMANDS", "main"]


def main(argv=None, *, generate=None) -> int:
    """Run the command named, or `rewrite` when the arguments name no other one.

    Returns 0 when every file was read and rewritten, 1 when a file could not be
    handled. Comments that were deliberately skipped or that the model failed to
    rewrite are reported, but they are not errors. A model or a skill that cannot
    be set up at all — pi is not installed, the vendored skill is damaged — is 2,
    because nothing was attempted. An interrupt stops the run between files and
    returns 128 plus the signal that caused it, after saying how far it got.
    """
    arguments = _parse(argv)
    try:
        return arguments.command_run(arguments, generate)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        silence_stdout()
        return PIPE_STATUS
    except (SkillError, RuntimeError) as error:
        print(f"commahum: {error}", file=sys.stderr)
        return 2


def _parse(argv):
    parser = _build_parser()
    return parser.parse_args(_with_default_command(list(sys.argv[1:] if argv is None else argv)))


def _with_default_command(argv: list[str]) -> list[str]:
    """A bare `commahum <paths>` is `rewrite`; a command or `--help` is itself.

    Only the first word decides. A file that is called `report` is reachable as
    `commahum rewrite report` or `commahum ./report`, and the ambiguity is the
    price of keeping the everyday invocation short.
    """
    if not argv or argv[0] in ("-h", "--help"):
        return argv or ["--help"]
    if argv[0] in COMMANDS:
        return argv
    return ["rewrite", *argv]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commahum",
        description=(
            "Rewrite the comments of Nix files in the style of the vendored humanizer-ru "
            "skill, asked of pi. Nothing is written without --write."
        ),
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    for name, (add_arguments, run, help_text) in COMMANDS.items():
        command = commands.add_parser(name, help=help_text, description=help_text)
        add_arguments(command)
        command.set_defaults(command_run=run)
    return parser


def run(arguments, generate=None) -> int:
    """The `rewrite` command: what the program was originally for."""
    skill = vendored_skill()
    if generate is None:
        generate = asking.generator(arguments, skill=skill)

    paths = collect(arguments.paths)
    totals = {
        "rewritten": 0,
        "skipped": 0,
        "kept": 0,
        "files": 0,
        "written": 0,
        "requests": 0,
    }
    failed = False
    stopped = None
    state = trap_interrupts()
    try:
        failed = _rewrite_all(paths, arguments, generate, totals, invocation=skill.invocation)
    except KeyboardInterrupt:
        stopped = 128 + (state["signal"] or signal.SIGINT)
        print(
            f"interrupted: {signal.Signals(state['signal'] or signal.SIGINT).name}, "
            f"stopped after {totals['files']} of {len(paths)} files, "
            f"{totals['written']} written",
            file=sys.stderr,
        )
    except BrokenPipeError:
        silence_stdout()
        stopped = PIPE_STATUS
    finally:
        restore_interrupts(state)

    print(_totals_line(totals), file=sys.stderr)
    if stopped is not None:
        return stopped
    return 1 if failed else 0


def add_arguments(parser) -> None:
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
    asking.add_options(parser, catalog=True)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print neither the request target nor the per-file progress, only the diff and the summary",
    )


def _rewrite_all(paths, arguments, generate, totals, *, invocation: str) -> bool:
    """Rewrite each path in turn; return True if any of them could not be handled.

    Interrupts are deliberately not caught here: they unwind through this
    function, and nothing has been written for the file in flight, so the run
    stops with every file either rewritten whole or untouched.

    A file that is no longer what was read — changed, or gone — is reported and
    left alone rather than written: clobbering the newer contents would be worse
    than one file less rewritten, and the run goes on to the rest.
    """
    failed = False
    for path in paths:
        if not path.is_file():
            print(f"{path}: no such file", file=sys.stderr)
            failed = True
            continue
        source = path.read_bytes()
        if not arguments.quiet:
            print(f"{display_name(path)}: {_progress_note(generate)}", file=sys.stderr)
        try:
            result = rewrite_source(source, generate, invocation=invocation)
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the rest
            print(f"{path}: {type(error).__name__}: {error}", file=sys.stderr)
            failed = True
            continue

        totals["files"] += 1
        totals["rewritten"] += len(result.applied)
        totals["skipped"] += len(result.skipped)
        totals["kept"] += len(result.unrewritten)
        totals["requests"] += result.requests

        diff = render_diff(source, result.rewritten, display_name(path))
        if diff:
            print(diff, end="" if diff.endswith("\n") else "\n")
        if arguments.write and result.rewritten != source:
            try:
                _write_atomically(path, result.rewritten, expected=source)
            except StaleFileError as stale:
                print(f"{path}: {stale}, not written", file=sys.stderr)
                failed = True
            else:
                totals["written"] += 1

        print(
            f"{path}: {len(result.applied)} rewritten, "
            f"{len(result.skipped)} skipped, {len(result.unrewritten)} kept",
            file=sys.stderr,
        )
        for number, reason in result.unrewritten:
            print(f"{path}: comment {number} kept: {reason}", file=sys.stderr)
    return failed


def _totals_line(totals) -> str:
    return (
        f"{totals['files']} files, {totals['rewritten']} comments rewritten, "
        f"{totals['skipped']} skipped, {totals['kept']} kept, "
        f"{totals['requests']} requests"
    )


def _progress_note(generate) -> str:
    """What is about to happen to this file, in the terms the run was asked in.

    A generator that names its target (``deepseek · deepseek-flash``) says so; one
    that does not still gets a line, because a run over a directory is otherwise
    silent while a request is in flight.
    """
    target = getattr(generate, "description", None)
    return f"asking {target}" if target else "rewriting"


class StaleFileError(Exception):
    """The file on disk is no longer the one the rewrite was computed from."""


def _write_atomically(path, data: bytes, *, expected: bytes) -> None:
    """Replace ``path`` with ``data`` in one step, keeping its mode.

    A config file caught halfway through a write is worse than one that was not
    rewritten at all, so the new bytes go to a temporary file first and are moved
    into place with a single ``os.replace``. A reader sees either the old file or
    the new one, never a truncated one.

    ``expected`` is the content the rewrite was computed from. It is re-read
    immediately before the move, and a file that no longer matches — or that can
    no longer be read — is left alone and reported as stale instead of being
    replaced. The comparison is on the bytes, so a concurrent save that changes
    no byte is not a conflict: there is nothing there to lose.

    This narrows the race, it does not close it: the re-read and the move are
    still two steps, and an edit landing in the sliver between them is still
    overwritten. There is no compare-and-swap here to claim — what the re-read
    buys is the wide window, the model request, where a save is likely to land.
    """
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        raise StaleFileError("gone since it was read") from None
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(handle, mode)
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            current = path.read_bytes()
        except OSError:
            raise StaleFileError("gone since it was read") from None
        if current != expected:
            raise StaleFileError("changed since it was read")
        os.replace(temp_name, path)
    except BaseException:
        # BaseException, not Exception: an interrupt landing between the temporary
        # file and the move would otherwise leave the temporary file behind, next
        # to a config file, looking like something that belongs there.
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


#: Every command, as (name, add_arguments, run, one-line help). The order is the
#: order of the help; `rewrite` is first because it is what a bare run means.
COMMANDS = {
    "rewrite": (
        add_arguments,
        run,
        "rewrite comments and show the diff, writing nothing without --write (the default)",
    ),
    "report": (
        report.add_arguments,
        report.run,
        "rewrite, then grade the result with the skill's own scanner",
    ),
    "measure": (
        measure.add_arguments,
        measure.run,
        "run a corpus and print the numbers; --save a baseline, --baseline to compare",
    ),
    "probe": (
        probe.add_arguments,
        probe.run,
        "check that the vendored skill really reaches the model, with rigged markers",
    ),
    "auth": (
        auth.add_arguments,
        auth.run,
        "what pi is ready for, where the credential comes from, and how to store one",
    ),
    "check": (
        selfcheck.add_arguments,
        selfcheck.run,
        "verify the comment-only invariant with a stand-in model: no model, no network",
    ),
}
