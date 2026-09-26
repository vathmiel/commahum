"""Run the narrow Codex adapter tests and live smoke check."""
import importlib.metadata
import os
import sys
import tempfile
from pathlib import Path

from commahum.codex_adapter import CodexAuthError, CodexSubscriptionClient, HermesTokenStore


def main() -> None:
    try:
        importlib.metadata.distribution("hermes-agent")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError("Run this smoke test from a venv without Hermes Agent")

    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in {"success", "missing-auth"}:
        raise SystemExit("usage: smoke.py success|missing-auth")

    if mode == "missing-auth":
        temporary = tempfile.TemporaryDirectory()
        os.environ["HOME"] = temporary.name
        os.environ["HERMES_HOME"] = str(Path(temporary.name) / ".hermes")

    client = CodexSubscriptionClient(token_store=HermesTokenStore(), model="gpt-5.5")
    if mode == "missing-auth":
        try:
            client.complete("ping", instructions="Reply with exactly OK.")
        except CodexAuthError:
            print("No credentials: adapter fails closed")
            return
        raise AssertionError("Missing credentials unexpectedly succeeded")

    result = client.complete("ping", instructions="Reply with exactly OK.")
    assert result == "OK", repr(result)
    print("Codex subscription request: OK; Hermes package not installed")


if __name__ == "__main__":
    main()
