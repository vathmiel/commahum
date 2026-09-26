"""Stopping a run that is in flight, without leaving a file half-written.

A signal arrives between two bytecode instructions, so it unwinds the run exactly
where it landed: between files, or inside one — where the write is atomic anyway,
so every file is either rewritten whole or left alone. The first signal is
remembered so the exit status can name it; a second one gives up on the summary
and leaves at once.

`rewrite` traps these around its own loop because only it can say how far the run
got. A command that does not trap them still ends cleanly: the interpreter's
KeyboardInterrupt reaches `cli.main`, which reports the interrupt in one line.
"""

from __future__ import annotations

import os
import signal
import sys

__all__ = ["PIPE_STATUS", "restore_interrupts", "silence_stdout", "trap_interrupts"]

#: What a shell reports for a process whose reader closed the pipe.
PIPE_STATUS = 141


def trap_interrupts() -> dict:
    """Turn SIGINT, SIGTERM and SIGHUP into a KeyboardInterrupt at the current point.

    Signals belong to the main thread, so a caller from a thread gets back a state
    with nothing installed and keeps its own handlers.
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


def restore_interrupts(state) -> None:
    """Hand the signals back as they were, so a caller embedding this stays as it was."""
    for signum, previous in state.get("previous", {}).items():
        try:
            signal.signal(signum, previous)
        except ValueError:  # pragma: no cover - only reachable off the main thread
            return


def silence_stdout() -> None:
    """Point stdout at /dev/null, so the interpreter's last flush cannot raise again."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError):
        pass
