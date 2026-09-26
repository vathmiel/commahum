"""The model call, made through pi instead of an adapter of this project.

`pi <https://pi.dev>` owns the provider plumbing: the per-provider catalogs, API
keys and OAuth for subscription plans, retries, and the wire protocols. This
module only runs it as a program — pi prints the final assistant text and this
reads it:

    pi --print --no-tools --no-session --system-prompt <rules> < numbered prose

The run is hermetic on purpose. Tools, sessions, context files (``AGENTS.md``),
skills, extensions and prompt templates are all switched off, and telemetry and
update checks go with them, so the model sees the numbered prose and our
instructions and nothing else. What the pipeline promises about the payload
stays true because pi is given nothing to add to it. The provider catalog and
credential refresh are left to pi: they are its business, and a stale token
should be pi's problem to solve, not a silence we imposed.

The prose goes to stdin, never to the command line, so the text under review does
not end up in the process table.

Rules are not this project's text either: the vendored skill is handed to pi with
``--skill`` and invoked at the start of the prompt, with the name followed by a
space, and a prompt that does not open that way is refused before any process is
started, so a run cannot quietly fall back to rewriting by taste. The companion
files the flow reads around an edit travel as ``--append-system-prompt``
arguments.

Where pi comes from: ``$COMMAHUM_PI`` if it is set, otherwise the copy vendored
under ``vendor/pi/`` and run with the node on ``PATH``, otherwise a ``pi`` on
``PATH``. The vendored copy is what this project tests against, so a checkout
works without installing anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from .skill import FLOW_READING, Skill, vendored_skill

__all__ = [
    "ISOLATION",
    "ENVIRONMENT",
    "build_argv",
    "pi_command",
    "pi_generator",
    "vendored_entry",
]

#: The entry point of the vendored copy, relative to the repository root.
VENDORED_ENTRY = ("vendor", "pi", "dist", "bundle", "cli.js")

#: Flags that keep a run to the one thing we ask for.
ISOLATION = (
    "--no-tools",
    "--no-session",
    "--no-context-files",
    "--no-skills",
    "--no-extensions",
    "--no-prompt-templates",
)

#: Quiet switches pi documents. ``PI_OFFLINE`` is deliberately not set: it would
#: also stop pi from refreshing its model catalog and its own credentials.
ENVIRONMENT = {
    "PI_SKIP_VERSION_CHECK": "1",
    "PI_TELEMETRY": "0",
}

#: A file is one request; pi does its own retrying inside it.
TIMEOUT_SECONDS = 300

#: What a failed run says when the reason looks like credentials.
_AUTH_HINTS = ("auth", "token", "credential", "api key", "401", "403", "login")


def vendored_entry() -> Path | None:
    """``vendor/pi/dist/bundle/cli.js`` of this checkout, if it is there.

    Found by walking up from this file, so an editable install and a plain
    checkout both find it. Returns ``None`` rather than guessing a path: a
    checkout without the vendored copy falls through to whatever pi the host
    has, and says so if there is none.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*VENDORED_ENTRY)
        if candidate.is_file():
            return candidate
    return None


def pi_command() -> list[str]:
    """How to run pi, as the prefix of one request's command line.

    The vendored copy needs node >= 22.19.0, which pi's own ``engines`` field
    asks for; a host installation brings whatever runtime it ships with.
    """
    override = os.environ.get("COMMAHUM_PI")
    if override:
        return [override]
    entry = vendored_entry()
    if entry is not None:
        node = shutil.which("node")
        if node is None:
            raise RuntimeError(
                f"the vendored pi at {entry} runs on node, and no node is on PATH "
                "(pi needs >= 22.19.0)"
            )
        return [node, str(entry)]
    found = shutil.which("pi")
    if not found:
        raise RuntimeError(
            "no pi to run: this checkout has no vendor/pi/ and no pi is on PATH; "
            "install one (nix profile install nixpkgs#pi-coding-agent) or point "
            "$COMMAHUM_PI at the executable"
        )
    return [found]


def build_argv(
    command: Sequence[str],
    *,
    model: str | None = None,
    provider: str | None = None,
    instructions: str,
    skill: Skill | None = None,
    readings: Sequence[str] = (),
) -> list[str]:
    """The command line for one request: prompt on stdin, rules as the system prompt."""
    argv = [*command, "--print", *ISOLATION]
    if skill is not None:
        argv += ["--skill", str(skill.directory)]
        for relative in readings:
            argv += ["--append-system-prompt", str(skill.file(relative))]
    if provider:
        argv += ["--provider", provider]
    if model:
        argv += ["--model", model]
    argv += ["--system-prompt", instructions]
    return argv


def pi_generator(
    model: str | None = None,
    provider: str | None = None,
    *,
    instructions: str,
    skill: Skill | None = None,
    readings: Sequence[str] = FLOW_READING,
    run=subprocess.run,
):
    """A ``generate(prompt) -> text`` callable that asks pi.

    ``model`` and ``provider`` are passed through as pi understands them:
    ``deepseek/deepseek-flash``, ``sonnet:high``, or a bare provider name whose
    own default model is then used.

    ``skill`` is the vendored style skill and must be invoked by every prompt;
    ``readings`` are its companion files, appended to pi's system prompt.
    """
    skill = skill or vendored_skill()
    argv = build_argv(
        pi_command(),
        model=model,
        provider=provider,
        instructions=instructions,
        skill=skill,
        readings=readings,
    )

    def generate(prompt: str) -> str:
        if not prompt.startswith(skill.opening):
            raise RuntimeError(
                f"refusing to ask without the skill: the prompt must start with "
                f"{skill.opening!r} (pi reads the skill name up to the first space, "
                f"and a name it cannot find is passed through without a word)"
            )
        try:
            completed = run(
                argv,
                input=prompt.encode("utf-8", "surrogateescape"),
                capture_output=True,
                timeout=TIMEOUT_SECONDS,
                env={**os.environ, **ENVIRONMENT},
            )
        except subprocess.TimeoutExpired as expired:
            raise RuntimeError(f"pi did not answer within {TIMEOUT_SECONDS}s") from expired
        if completed.returncode != 0:
            raise RuntimeError(_failure(completed))
        return completed.stdout.decode("utf-8", "surrogateescape")

    if model is None:
        # Naming only a provider does not make pi ask that provider's model — it
        # still answers with the model it is configured with. Say so rather than
        # implying a model that is not the one being called.
        named = f"{provider} (model from pi's config)" if provider else "model from pi's config"
    elif "/" in model:
        # pi reads this as provider/id already; prefixing it again would double it.
        named = model
    else:
        named = f"{provider}/{model}" if provider else model
    generate.description = f"pi · {named} · {skill.name}"  # type: ignore[attr-defined]
    return generate


def _failure(completed) -> str:
    """pi's own last words, so the reason survives without the whole dump."""
    lines = [
        line.strip()
        for line in completed.stderr.decode("utf-8", "surrogateescape").splitlines()
        if line.strip()
    ]
    said = " / ".join(lines[-3:]) or "no output"
    hint = ""
    if any(word in said.lower() for word in _AUTH_HINTS):
        hint = " — check `pi auth check --provider <name>`"
    return f"pi exited {completed.returncode}: {said}{hint}"
