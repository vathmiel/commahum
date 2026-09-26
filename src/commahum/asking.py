"""How a command asks: the options every command shares, and the one generator.

Three commands talk to a model — `rewrite`, `report` and `probe` — and they must
agree on what they ask with. The rules are the vendored skill's, not a paraphrase
of it: every request carries the skill itself, and the files the flow reads around
a rewrite. A command that forgot one of those, or spelled the invocation
differently, would still run and still produce a plausible diff, which is exactly
how this project lost a week to a skill that never reached the model. So the
options and the generator live in one place, and no command assembles its own.
"""

from __future__ import annotations

from .pi_runner import pi_generator
from .rewrite import INSTRUCTIONS
from .skill import CATALOG, FLOW_READING

__all__ = ["add_options", "generator"]


def add_options(parser, *, catalog: bool = False) -> None:
    """Add `--model` and `--provider` to ``parser``, and `--catalog` if it wants one."""
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "model for pi, in pi's own words: provider/id and a thinking level after "
            "a colon (deepseek/deepseek-flash, openai-codex/gpt-5.5, sonnet:high). "
            "Naming the provider here is what picks it"
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "provider for pi (deepseek, openai-codex, anthropic, ...); pi still asks "
            "the model it is configured with, so prefer --model provider/id when the "
            "provider matters"
        ),
    )
    if catalog:
        parser.add_argument(
            "--catalog",
            action="store_true",
            help=(
                "also send the skill's whole pattern catalogue (93 KB, about 25k tokens per "
                "request); the skill reaches for it only when a pattern is unclear"
            ),
        )


def generator(arguments, *, skill, readings=None):
    """Ask pi, which owns the providers, their credentials and the default model.

    ``readings`` defaults to the files the flow reads, plus the catalogue when
    `--catalog` was given; the probe passes its own because its copy is rigged.
    """
    if readings is None:
        readings = (*FLOW_READING, CATALOG) if arguments.catalog else FLOW_READING
    return pi_generator(
        arguments.model,
        arguments.provider,
        instructions=INSTRUCTIONS,
        skill=skill,
        readings=readings,
    )
