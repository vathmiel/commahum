"""Run the copied Codex path from a clean venv: `PYTHONPATH=vendor .venv/bin/python smoke.py success`."""
import importlib.metadata
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    try:
        importlib.metadata.distribution("hermes-agent")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError("This smoke test requires a venv without Hermes Agent")

    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in {"success", "missing-auth"}:
        raise SystemExit("usage: smoke.py success|missing-auth")

    # For the negative case, don't read the real user's OAuth store or CLI login.
    if mode == "missing-auth":
        temporary = tempfile.TemporaryDirectory()
        os.environ["HOME"] = temporary.name
        os.environ["HERMES_HOME"] = str(Path(temporary.name) / ".hermes")
        os.environ["CODEX_HOME"] = str(Path(temporary.name) / ".codex")

    from agent.auxiliary_client import resolve_provider_client

    client, model = resolve_provider_client("openai-codex", model="gpt-5.5")
    if mode == "missing-auth":
        assert client is None and model is None
        print("No credentials: explicit None, no provider fallback")
        return

    assert client is not None and model == "gpt-5.5"
    result = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Reply with exactly OK."},
            {"role": "user", "content": "ping"},
        ],
        timeout=60,
    )
    assert result.choices[0].message.content == "OK"
    print("Codex subscription request: OK; Hermes package not installed")


if __name__ == "__main__":
    main()
