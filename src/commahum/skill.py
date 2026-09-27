"""The style skill: where it is, what it is called, and what the flow reads.

The rules are not this project's text. They are `vendor/humanizer-ru/`, a copy of
the skill pinned to its upstream tag, and this module is the single place that
knows how to find it, what to call it by and where its companion files are.

Where the copy is depends on how the project was installed. A verified checkout
keeps it at `vendor/humanizer-ru/`; an installed wheel keeps it inside the
package, at `commahum/_vendor/humanizer-ru/`. An installed package never uses
an ancestor's `vendor/` to replace a missing packaged file.

Two guarantees live here, both fail-closed:

* the skill has to be there and has to have a usable frontmatter, because a
  request that runs without rules would quietly rewrite comments by taste;
* the invocation name (`/skill:humanizer-ru`) is read out of the skill's own
  `name` field rather than hardcoded, so a rename upstream breaks loudly here
  instead of producing requests that call a skill nobody has.

`SKILL.md` is the part pi loads by itself when the skill is invoked. The flow
also tells the editor to read `edit-log.md` before the work and
`references/review.md` for the cold re-read afterwards; those two are small
enough to travel with every request, and `references/catalog.md` — the full
pattern catalogue — is offered only when asked for, because the flow itself
opens it on demand rather than always.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = [
    "CATALOG",
    "FLOW_READING",
    "PACKAGED_VENDOR",
    "Skill",
    "SkillError",
    "catalog",
    "vendored_path",
    "vendored_skill",
]

#: Where the skill lives inside a vendor root: `vendor/humanizer-ru/SKILL.md` in a
#: checkout, `commahum/_vendor/humanizer-ru/SKILL.md` in an installed package.
VENDORED_SKILL = ("vendor", "humanizer-ru")

#: What the package calls the vendor root it carries itself. A wheel cannot keep
#: `vendor/` at the top of site-packages — that is nobody's to own — so the trees
#: are mapped into the package at build time and looked for there first.
PACKAGED_VENDOR = "_vendor"

#: What the flow reads around the edit itself: before it (edit-log.md) and for
#: the cold re-read after it (references/review.md).
FLOW_READING = ("edit-log.md", "references/review.md")

#: The catalogue of all patterns. The flow opens it only when a pattern is
#: unclear, so it travels with a request only when it was asked for.
CATALOG = "references/catalog.md"

_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class SkillError(RuntimeError):
    """The skill is missing, or is not a skill this project can invoke."""


@dataclass(frozen=True)
class Skill:
    """A skill on disk: its directory, its name, and what its frontmatter says."""

    directory: Path
    name: str
    description: str
    license: str | None = None

    @property
    def invocation(self) -> str:
        """How pi is asked to load it, as the first line of a prompt."""
        return f"/skill:{self.name}"

    @property
    def opening(self) -> str:
        """The exact prefix a prompt needs, and why it is not just the invocation.

        pi expands a skill command like this: it takes everything from after
        ``/skill:`` up to **the first space in the whole prompt** as the name, looks
        that name up, and — if it finds nothing — returns the prompt untouched. No
        warning, no error, and the run continues without any rules. A prompt that
        puts a newline right after the name hands pi a name like
        ``humanizer-ru\\nКомментарии:`` and silently loads nothing, so the name has
        to be followed by a space, with the prose behind it on the same line.
        """
        return f"{self.invocation} "

    def file(self, relative: str) -> Path:
        """A companion file of the skill, refused when it is not there."""
        path = self.directory / relative
        if not path.is_file():
            raise SkillError(f"{self.name}: no {relative} in {self.directory}")
        return path

    def reading(self, relative: str) -> str:
        """A companion file's text, refused when it is not there."""
        return self.file(relative).read_text(encoding="utf-8")


def vendored_skill() -> Skill:
    """The skill vendored in this checkout, parsed from its own `SKILL.md`."""
    return _parse(_locate())


def catalog(skill: Skill) -> Path:
    """The full pattern catalogue, for the runs that ask for it."""
    return skill.file(CATALOG)


def vendored_path(relative: str) -> Path | None:
    """A file of the vendored trees, wherever this installation keeps them.

    ``relative`` is spelled the way a checkout spells it — ``humanizer-ru/SKILL.md``,
    ``pi/dist/bundle/cli.js`` — and only a checkout can fall back to its vendor
    tree. A wheel inside somebody else's repository uses only its packaged copy.
    """
    for root in _vendor_roots():
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def _vendor_roots() -> list[Path]:
    """Use the package's own tree, or the vendor tree of a verified checkout.

    Never walk arbitrary ancestors: site-packages may be inside someone else's
    repository, and missing packaged files must not come from that repository.
    """
    package = Path(__file__).resolve().parent
    packaged = package / PACKAGED_VENDOR
    if packaged.exists():
        return [packaged]

    roots = [packaged]
    if package.parent.name == "src":
        checkout = package.parent.parent
        pyproject = checkout / "pyproject.toml"
        if pyproject.is_file() and tomllib.loads(pyproject.read_text(encoding="utf-8")).get(
            "project", {}
        ).get("name") == "commahum":
            roots.append(checkout / VENDORED_SKILL[0])
    return roots


def _locate() -> Path:
    """The vendored `SKILL.md`: the packaged copy, else a verified checkout's.

    Missing is an error rather than an empty result: without the skill there are
    no rules to follow, and a pipeline that keeps going would be inventing them.
    """
    found = vendored_path("/".join((*VENDORED_SKILL[1:], "SKILL.md")))
    if found is None:
        raise SkillError(
            "no vendored skill: expected "
            + "/".join(VENDORED_SKILL)
            + "/SKILL.md in a checkout, or "
            + "/".join((PACKAGED_VENDOR, *VENDORED_SKILL[1:], "SKILL.md"))
            + " inside an installed package"
        )
    return found


@lru_cache(maxsize=8)
def _parse(path: Path) -> Skill:
    fields, body = _frontmatter(path)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if not name:
        raise SkillError(f"{path}: frontmatter has no name")
    if not _NAME.match(name):
        raise SkillError(f"{path}: {name!r} is not a usable skill name")
    if not description:
        raise SkillError(f"{path}: frontmatter has no description")
    return Skill(
        directory=path.parent,
        name=name,
        description=description,
        license=fields.get("license") or None,
    )


def _frontmatter(path: Path) -> tuple[dict[str, str], str]:
    """Split `SKILL.md` into its frontmatter fields and the body.

    Deliberately small: the standard's frontmatter here is flat `key: value`
    lines, and a YAML dependency for five of them would be a library to keep
    current. Anything that is not such a line is refused rather than guessed at.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"{path}: no frontmatter")
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return fields, "\n".join(lines[index + 1 :])
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise SkillError(f"{path}: line {index + 1} is not a `key: value` pair")
        fields[key.strip()] = _unquote(value.strip())
    raise SkillError(f"{path}: frontmatter is never closed")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value
