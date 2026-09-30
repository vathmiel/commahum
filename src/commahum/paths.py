"""Turning command-line arguments into the files a command will work on.

Two commands take paths — `rewrite` and `report` — and they must agree on what a
path means: a file is that file, a directory is every `*.nix` under it, in sorted
order, so that two runs over the same tree ask the model the same questions in the
same order.
"""

from __future__ import annotations

from pathlib import Path
import os

__all__ = ["collect", "display_name"]


def collect(paths) -> list[Path]:
    """The files named, with every directory expanded to its `*.nix`, in order."""
    files: list[Path] = []
    seen: set[str] = set()
    for argument in paths:
        path = Path(argument)
        expanded = sorted(path.rglob("*.nix")) if path.is_dir() else [path]
        for selected in expanded:
            # Resolve directory aliases and '..', but retain the final path
            # component: a symbolic link is a distinct write target and must
            # not hide an explicitly named regular file.
            identity = os.path.normcase(str(selected.parent.resolve() / selected.name))
            if identity not in seen:
                seen.add(identity)
                files.append(selected)
    return files


def display_name(path: Path) -> str:
    """A short name for the diff headers: relative to the working directory if it can be."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path).lstrip("/")
