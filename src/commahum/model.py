"""A model preference for Commahum alone, independent of pi's settings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base and Path(base).is_absolute() else Path.home() / ".config") / "commahum" / "config.json"


def _validate(value: object) -> str:
    if (
        not isinstance(value, str)
        or value.count("/") != 1
        or not all(value.split("/"))
        or any(not character.isprintable() or character.isspace() for character in value)
    ):
        raise RuntimeError("model must be provider/id (for example deepseek/deepseek-flash)")
    return value


def saved() -> str | None:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        reason = error.strerror or type(error).__name__ if isinstance(error, OSError) else str(error)
        raise RuntimeError(f"cannot read {str(path)!r}: {reason}") from error
    if not isinstance(data, dict) or set(data) != {"model"}:
        raise RuntimeError(f"invalid model preference in {str(path)!r}")
    try:
        return _validate(data["model"])
    except RuntimeError as error:
        raise RuntimeError(f"invalid model preference in {str(path)!r}: {error}") from error


def save(value: str) -> None:
    value = _validate(value)
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".config.", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"model": value}, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    except OSError as error:
        raise RuntimeError(
            f"cannot save {str(path)!r}: {error.strerror or type(error).__name__}"
        ) from error


def clear() -> None:
    path = config_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise RuntimeError(
            f"cannot clear {str(path)!r}: {error.strerror or type(error).__name__}"
        ) from error


def add_arguments(parser) -> None:
    parser.add_argument("action", nargs="?", choices=("show", "set", "clear"), default="show")
    parser.add_argument("value", nargs="?", help="provider/id, for example deepseek/deepseek-flash")


def run(arguments, generate=None) -> int:
    if arguments.action == "set":
        if not arguments.value:
            raise RuntimeError("model set requires provider/id")
        save(arguments.value)
        print(f"model: {arguments.value} (saved for commahum)")
    elif arguments.value is not None:
        raise RuntimeError(f"model {arguments.action} takes no value")
    elif arguments.action == "clear":
        clear()
        print("model: pi default (preference cleared)")
    else:
        print(f"model: {saved() or 'pi default (not set)'}")
    return 0
