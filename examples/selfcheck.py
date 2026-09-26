"""Self-check: prove that rewriting comments never changes a non-comment byte.

The rewriter here is a stand-in (uppercase) — the real one calls a language
model. What matters is the invariant around it, and the invariant is checked
the same way either way:

  1. parse the file and splice rewritten comments back by byte range
  2. re-parse the result
  3. compare every non-comment byte run of the old and the new file

Use it as a smoke test over a real config tree:

    python examples/selfcheck.py /etc/nixos --quiet
    python examples/selfcheck.py /etc/nixos/flake.nix
"""

from __future__ import annotations

import sys
from pathlib import Path

from commahum.comments import find_comment_spans
from commahum.diff import render_diff
from commahum.patch import Rewrite, apply_rewrites


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


def main(argv: list[str]) -> int:
    arguments = [arg for arg in argv if not arg.startswith("--")]
    quiet = "--quiet" in argv
    if not arguments:
        print(__doc__)
        return 2

    files: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.nix")))
        else:
            files.append(path)

    total_comments = 0
    total_violations = 0
    for path in files:
        try:
            comments, violations = selfcheck(path, quiet=quiet)
        except Exception as error:  # noqa: BLE001 - report and keep going
            print(f"{path}: ERROR {type(error).__name__}: {error}", file=sys.stderr)
            total_violations += 1
            continue
        total_comments += comments
        total_violations += violations
        if not quiet:
            print(f"{path}: {comments} comments, {violations} violations", file=sys.stderr)

    print(
        f"\n{len(files)} files, {total_comments} comments rewritten, "
        f"{total_violations} invariant violations",
        file=sys.stderr,
    )
    return 1 if total_violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
