"""Where the vendored Hermes provider layer lives, and how it is loaded.

`vendor/hermes/` is a copy of Hermes' modules, not an installed distribution.
Two things follow, and both are enforced here rather than hoped for:

* a vendored name must resolve inside the tree or not at all — an installed
  Hermes in the same environment must not quietly serve the request instead;
* a module missing from the tree must raise, not degrade. That distinction is
  the whole reason for the fail-closed finder: without it the provider layer
  keeps importing happily while a provider silently loses its profile, its
  default model and its aliases (exactly how the DeepSeek path broke before the
  profiles were vendored).

`tools/closure_probe.py` keeps the tree complete at development time; this
module is what the runtime uses.
"""

from __future__ import annotations

import importlib.abc
import os
import sys
from pathlib import Path

__all__ = ["tree_root", "vendored_roots", "install_fail_closed_finder", "activate", "ENV_VAR"]

#: Point the runtime at another copy of the tree (a wheel install has no source
#: tree beside it).
ENV_VAR = "COMMAHUM_HERMES_TREE"

_FINDERS: set[str] = set()


def tree_root() -> Path:
    """The vendored tree: ``$COMMAHUM_HERMES_TREE``, else ``vendor/hermes`` beside the source."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "vendor" / "hermes"


def vendored_roots(root: Path | None = None) -> set[str]:
    """Top-level names the tree owns: ``agent``, ``hermes_cli``, ``providers``, …"""
    root = root or tree_root()
    if not root.is_dir():
        raise FileNotFoundError(f"no vendored Hermes tree at {root} (set {ENV_VAR} to point elsewhere)")

    names: set[str] = set()
    for path in root.iterdir():
        if path.is_dir() and (path / "__init__.py").exists():
            names.add(path.name)
        elif path.suffix == ".py":
            names.add(path.stem)
    return names


def install_fail_closed_finder(root: Path | None = None) -> None:
    """Make a vendored name resolve inside the tree, or raise.

    A name the tree owns that has no file in the tree is reported immediately
    instead of falling through to an installed copy.
    """
    resolved = (root or tree_root()).resolve()
    key = str(resolved)
    if key in _FINDERS:
        return
    roots = vendored_roots(resolved)
    files = {path.relative_to(resolved).as_posix() for path in resolved.rglob("*.py")}

    class VendoredOnly(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002 - finder protocol
            if fullname.split(".")[0] not in roots:
                return None
            relative = Path(*fullname.split("."))
            if f"{relative.as_posix()}.py" in files or f"{relative.as_posix()}/__init__.py" in files:
                return None  # present: the ordinary finder loads it from the tree
            raise ModuleNotFoundError(f"Hermes module absent from the vendored tree: {fullname}")

    sys.meta_path.insert(0, VendoredOnly())
    _FINDERS.add(key)


def activate(root: Path | None = None) -> Path:
    """Put the tree first on ``sys.path`` and make it authoritative. Returns the root."""
    resolved = (root or tree_root()).resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"no vendored Hermes tree at {resolved} (set {ENV_VAR} to point elsewhere)")
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
    install_fail_closed_finder(resolved)
    return resolved
