"""The `check` command: prove that rewriting comments never changes a non-comment byte.

The rewriter here is a stand-in (uppercase) — the real one calls a language model.
What matters is the invariant around it, and the invariant is checked the same way
either way:

  1. parse the file and splice rewritten comments back by byte range
  2. re-parse the result
  3. compare every non-comment byte run of the old and the new file

Which makes this the one command that needs neither a model nor the network, and
the one to run over a real config tree before trusting anything else:

    commahum check /etc/nixos --quiet
    commahum check /etc/nixos/flake.nix
"""

from __future__ import annotations

import sys
from pathlib import Path

from .comments import find_comment_spans
from .diff import render_diff
from .patch import Rewrite, apply_rewrites
from .paths import collect

__all__ = ["add_arguments", "code_segments", "run", "selfcheck", "stand_in_rewriter"]


def add_arguments(parser) -> None:
    parser.add_argument("paths", nargs="+", help="files to check, or directories to walk for *.nix")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print neither the diff nor the per-file line, only the summary",
    )


def stand_in_rewriter(text: bytes) -> bytes:
    """Pretend to rewrite a comment. Structure preserved, wording changed."""
    return text.decode("utf-8", "surrogateescape").upper().encode("utf-8", "surrogateescape")


def code_segments(source: bytes) -> list[bytes]:
    """Every non-comment byte run in ``source``, in order."""
    segments: list[bytes] = []
    position = 0
    for span in find_comment_spans(source):
        segments.append(source[position : span.start_byte])
        position = span.end_byte
    segments.append(source[position:])
    return segments


def selfcheck(path: Path, *, quiet: bool = False) -> tuple[int, int]:
    """Rewrite every comment in ``path`` and verify the invariant.

    Returns ``(comments_rewritten, invariant_violations)``.
    """
    source = path.read_bytes()
    spans = [span for span in find_comment_spans(source) if not span.text.startswith(b"#!")]
    rewrites = [Rewrite(span, stand_in_rewriter(span.text)) for span in spans]
    rewritten = apply_rewrites(source, rewrites)

    violations = 0
    if code_segments(source) != code_segments(rewritten):
        violations += 1
    if len(find_comment_spans(rewritten)) != len(find_comment_spans(source)):
        violations += 1

    if not quiet:
        diff = render_diff(source, rewritten, path.name)
        if diff:
            print(diff, end="" if diff.endswith("\n") else "\n")
    return len(rewrites), violations


def run(arguments, generate=None) -> int:
    """The generator is not used: the rewriter here is a stand-in on purpose."""
    files = collect(arguments.paths)
    total_comments = 0
    total_violations = 0
    for path in files:
        try:
            comments, violations = selfcheck(path, quiet=arguments.quiet)
        except Exception as error:  # noqa: BLE001 - report and keep going
            print(f"{path}: ERROR {type(error).__name__}: {error}", file=sys.stderr)
            total_violations += 1
            continue
        total_comments += comments
        total_violations += violations
        if not arguments.quiet:
            print(f"{path}: {comments} comments, {violations} violations", file=sys.stderr)

    print(
        f"\n{len(files)} files, {total_comments} comments rewritten, "
        f"{total_violations} invariant violations",
        file=sys.stderr,
    )
    return 1 if total_violations else 0
