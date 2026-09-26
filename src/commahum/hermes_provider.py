"""Model requests through the vendored Hermes provider layer.

The rewriting pipeline needs one thing from a model: ``prompt -> text``.
Everything else — which provider, which endpoint, where the credential lives,
which model suits an auxiliary task — is Hermes' business, and the vendored tree
is what knows it. Adding a provider is therefore a matter of having its profile
in ``vendor/hermes/plugins/model-providers/`` and a credential it can find, not
of writing another adapter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .hermes_tree import activate
from .rewrite import INSTRUCTIONS

__all__ = [
    "provider_generator",
    "resolve_provider_client",
    "load_credential_environment",
    "DEFAULT_TIMEOUT",
]

#: Comment rewriting answers are a few kilobytes; anything past this is a hang.
DEFAULT_TIMEOUT = 120.0

#: Where Hermes keeps per-home credentials when they are not exported.
ENV_FILE = ".env"


def load_credential_environment(home: Path | None = None) -> Path | None:
    """Load ``$HERMES_HOME/.env`` the way Hermes' own CLI does at startup.

    Credentials for API-key providers live there (``DEEPSEEK_API_KEY`` and
    friends). Hermes loads the file from its command line, so a library user such
    as this project has to do it itself — otherwise the provider resolves to no
    client and looks misconfigured while the key is sitting right there.

    A variable already present in the environment wins: an explicit export must
    not be silently overridden by a file. Values are never logged.
    """
    resolved = home or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    env_file = resolved / ENV_FILE
    if not env_file.is_file():
        return None

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip("'\"")
    return env_file


def resolve_provider_client(provider: str, model: str | None = None) -> tuple[Any, str | None]:
    """``(client, model)`` from the vendored tree: Hermes' own resolution, not a copy of it."""
    load_credential_environment()
    activate()
    from agent.auxiliary_client import resolve_provider_client as resolve

    return resolve(provider, model=model)


def provider_generator(
    provider: str,
    model: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    resolve: Callable[..., tuple[Any, str | None]] | None = None,
) -> Callable[[str], str]:
    """A ``generate(prompt) -> text`` callable for ``provider``.

    ``model`` is passed through to the provider layer; ``None`` lets the provider
    supply its own default. A client that resolves to no model at all is refused
    here rather than sent to the endpoint as an empty name.

    The callable carries ``description`` — ``deepseek · deepseek-flash`` — so the
    command line can say where the request is going instead of leaving the run
    silent while it waits.
    """
    resolver = resolve or resolve_provider_client
    client, resolved = resolver(provider, model)
    if client is None:
        raise RuntimeError(f"{provider}: no usable client — is a credential configured for it?")

    chosen = model or resolved
    if not chosen:
        raise RuntimeError(f"{provider}: no model was resolved; name one with --model")

    def generate(prompt: str) -> str:
        response = client.chat.completions.create(
            model=chosen,
            messages=[
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": prompt},
            ],
            timeout=timeout,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError(f"{provider}: the model returned no text")
        return content

    generate.description = f"{provider} · {chosen}"  # type: ignore[attr-defined]
    return generate
