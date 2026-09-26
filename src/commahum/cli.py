"""The command line.

The safe thing is the default: read the file, show what would change, write
nothing. `--write` is the opt-in, and even then the diff is printed, because the
diff is the thing a human is meant to look at.

Stopping is safe too. Ctrl+C, a termination signal or a reader that closed the
pipe end the run between files; each file is either rewritten whole or left
alone; the summary says how far the run got.
"""

from __future__ import annotations

import argparse
import os
import signal
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
    rewrite are reported, but they are not errors. An interrupt stops the run
    between files and returns 128 plus the signal that caused it, after saying
    how far it got.
    """
    parser = _build_parser()
    arguments = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if generate is None:
        generate = _generator_for(arguments)

    paths = _collect(arguments.paths)
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
    state = _trap_interrupts()
    try:
        failed = _rewrite_all(paths, arguments, generate, totals)
    except KeyboardInterrupt:
        stopped = 128 + (state["signal"] or signal.SIGINT)
        print(
            f"interrupted: {signal.Signals(state['signal'] or signal.SIGINT).name}, "
            f"stopped after {totals['files']} of {len(paths)} files, "
            f"{totals['written']} written",
            file=sys.stderr,
        )
    except BrokenPipeError:
        _silence_stdout()
        stopped = PIPE_STATUS
    finally:
        _restore_interrupts(state)

    print(_totals_line(totals), file=sys.stderr)
    if stopped is not None:
        return stopped
    return 1 if failed else 0


def _rewrite_all(paths, arguments, generate, totals) -> bool:
    """Rewrite each path in turn; return True if any of them could not be handled.

    Interrupts are deliberately not caught here: they unwind through this
    function, and nothing has been written for the file in flight, so the run
    stops with every file either rewritten whole or untouched.
    """
    failed = False
    for path in paths:
        if not path.is_file():
            print(f"{path}: no such file", file=sys.stderr)
            failed = True
            continue
        source = path.read_bytes()
        if not arguments.quiet:
            print(f"{_display_name(path)}: {_progress_note(generate)}", file=sys.stderr)
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


def codex_generator(model: str = DEFAULT_MODEL):
    """A generator that sends each payload to the Codex subscription endpoint."""
    from .codex_adapter import CodexSubscriptionClient, HermesTokenStore

    client = CodexSubscriptionClient(token_store=HermesTokenStore(), model=model)

    def generate(prompt: str) -> str:
        return client.complete(prompt, instructions=INSTRUCTIONS)

    generate.description = f"Codex subscription · {model}"  # type: ignore[attr-defined]
    return generate


def _generator_for(arguments):
    """The Codex subscription by default; any vendored provider with ``--provider``."""
    if arguments.provider:
        return provider_generator(arguments.provider, arguments.model)
    return codex_generator(arguments.model or DEFAULT_MODEL)


def _progress_note(generate) -> str:
    """What is about to happen to this file, in the terms the run was asked in.

    A generator that names its target (``deepseek · deepseek-flash``) says so; one
    that does not still gets a line, because a run over a directory is otherwise
    silent while a request is in flight.
    """
    target = getattr(generate, "description", None)
    return f"asking {target}" if target else "rewriting"


PIPE_STATUS = 141  # what a shell reports for a process whose reader closed the pipe


def _trap_interrupts() -> dict:
    """Turn SIGINT, SIGTERM and SIGHUP into a KeyboardInterrupt at the current point.

    A signal arrives between two bytecode instructions, so it unwinds the run
    exactly where it landed: between files, or inside one — where the write is
    atomic anyway. The first signal is remembered so the exit status can name it;
    a second one gives up on the summary and leaves at once.

    Signals belong to the main thread, so a caller from a thread gets back a
    state with nothing installed and keeps its own handlers.
    """
    state: dict = {"signal": None, "previous": {}}

    def handle(signum, _frame):
        if state["signal"] is not None:
            print("interrupted again: leaving now", file=sys.stderr, flush=True)
            os._exit(128 + signum)
            return  # os._exit does not return; this keeps the intent explicit
        state["signal"] = signum
        raise KeyboardInterrupt

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            state["previous"][signum] = signal.signal(signum, handle)
    except ValueError:  # not the main thread: the process' signals are not ours to take
        return {"signal": None, "previous": {}}
    return state


def _restore_interrupts(state) -> None:
    """Hand the signals back as they were, so a caller embedding this stays as it was."""
    for signum, previous in state.get("previous", {}).items():
        try:
            signal.signal(signum, previous)
        except ValueError:  # pragma: no cover - only reachable off the main thread
            return


def _silence_stdout() -> None:
    """Point stdout at /dev/null, so the interpreter's last flush cannot raise again."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError):
        pass


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
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print neither the request target nor the per-file progress, only the diff and the summary",
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
    except BaseException:
        # BaseException, not Exception: an interrupt landing between the temporary
        # file and the move would otherwise leave the temporary file behind, next
        # to a config file, looking like something that belongs there.
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
