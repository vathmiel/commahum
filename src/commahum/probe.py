"""The `probe` command: ask pi to answer with words that exist only in the files we handed it.

The unit tests prove the request is *built* with the skill: the flag, the opening,
the appended files. They cannot prove that pi loads any of it, and that gap has
already cost this project one silent failure — an invocation followed by a newline
made pi look up a skill named `humanizer-ru\\nКомментарии:`, match nothing, and pass
the prompt through untouched, no error anywhere.

Two instruments were tried before this one and thrown away:

* asking the model what it can see — it reported a skill and a version it did not
  have, and reported nothing when the file *was* there;
* asking for the version in the skill's heading — the model knows this public
  repository well enough to answer v3.30.0 with no skill loaded at all.

So the probe rigs the material instead: it copies the vendored skill into a
temporary directory and appends a token generated at run time. That token is
nowhere else on earth, so quoting it cannot be recall, luck or compliance with the
question's premise. A second token, written nowhere, is asked for as well: a model
that quotes everything fails that check, which keeps the first one honest.

Two requests, no network beyond the provider, one of them deliberately negative.

    commahum probe --model deepseek/deepseek-flash

Exit status 0 when the rigged files reached the model and the absent token did not,
1 otherwise.
"""

from __future__ import annotations

import pathlib
import secrets
import shutil
import sys
import tempfile

from . import asking
from .skill import FLOW_READING, Skill, vendored_skill

__all__ = ["BODY", "READING", "add_arguments", "rigged", "run", "tokens"]

#: Where the rigged tokens go, relative to the skill directory.
BODY = "SKILL.md"
READING = FLOW_READING[0]


def tokens() -> tuple[str, str, str]:
    """The two tokens that will be written, and the one that will not.

    Generated per run, so that no answer can come from anywhere but the files we
    just handed over. The probe outlives any single run, so the values are not
    constants a reader could find in the source.
    """
    return (
        "ТЕЛО-" + secrets.token_hex(4),
        "ДОПОЛНЕНИЕ-" + secrets.token_hex(4),
        "ОТСУТСТВУЕТ-" + secrets.token_hex(4),
    )


def rigged(directory: pathlib.Path, marks: dict[str, str]) -> Skill:
    """A copy of the vendored skill carrying markers the model cannot know."""
    copy = directory / "humanizer-ru"
    shutil.copytree(vendored_skill().directory, copy)
    for relative, token in marks.items():
        path = copy / relative
        path.write_text(
            path.read_text(encoding="utf-8") + f"\n\nКОНТРОЛЬНАЯ СТРОКА: {token}\n",
            encoding="utf-8",
        )
    original = vendored_skill()
    return Skill(directory=copy, name=original.name, description=original.description)


def add_arguments(parser) -> None:
    parser.add_argument(
        "--model",
        default=None,
        help="model for pi, in pi's own words (provider/id, a thinking level after a colon)",
    )
    parser.add_argument("--provider", default=None, help="provider for pi")


def run(arguments, generate=None) -> int:
    body_token, reading_token, absent_token = tokens()

    with tempfile.TemporaryDirectory(prefix="commahum-probe-") as workspace:
        skill = rigged(pathlib.Path(workspace), {BODY: body_token, READING: reading_token})
        if generate is None:
            generate = asking.generator(arguments, skill=skill, readings=FLOW_READING)
        print(f"rigged copy: {skill.directory}")
        print(f"  {BODY}: {body_token}")
        print(f"  {READING}: {reading_token}")
        print(f"  asked for but written nowhere: {absent_token}\n")
        print(f"asking {getattr(generate, 'description', 'pi')}\n")

        answer = generate(
            f"{skill.opening}"
            "Процитируй дословно строки, начинающиеся словами «КОНТРОЛЬНАЯ СТРОКА». "
            "Если такой строки нет, ответь одним словом: нет.\n"
        )
        print("body and reading:")
        print("   ", " ".join(answer.split())[:200])

        negative = generate(
            f"{skill.opening}"
            f"Процитируй дословно строку, начинающуюся словами «{absent_token}». "
            "Если такой строки нет, ответь одним словом: нет.\n"
        )
        print("token that was never written:")
        print("   ", " ".join(negative.split())[:200])

    checks = (
        ("the skill body reached the model", body_token in answer),
        ("the flow reading reached the model", reading_token in answer),
        ("a token that exists nowhere was not invented", absent_token not in negative),
    )
    print()
    failures = 0
    for name, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
        failures += not passed

    if failures:
        print(
            f"\n{failures} of {len(checks)} checks failed: the skill is not reaching the model. "
            "Look at how the prompt opens — pi reads the skill name up to the first space."
        )
        return 1
    print(f"\nall {len(checks)} checks passed: the rigged files reached the model, and only they.")
    return 0
