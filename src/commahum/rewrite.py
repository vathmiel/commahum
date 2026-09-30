"""The rewriting round trip: ask once per file, apply only what survives.

Everything the model is allowed to influence is text. It receives numbered prose
bodies — no code, no `#`, no `TODO:` marker, no delimiters — and returns numbered
prose. The assembly and the splice stay on this side of the line, and every
returned body has to pass the patcher's rules before it lands in the file.

A body that fails is retried once on its own, and if it fails again the comment
keeps its original text. Nothing is ever forced in. A body that drops, changes
or invents a technical value — a path, a link, a host, a number — fails the same
way: the fact lock is enforced here, not only graded afterwards.

The rules the model follows are the vendored skill's, not this file's: every
request opens with the skill's invocation line, and the skill's own text arrives
with it. What is left here is the role, the register and the reply protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .bans import introduced_bans
from .comments import CommentError, CommentSpan
from .patch import Rewrite, apply_rewrites
from .selection import Candidate, select_candidates
from .values import value_drift

__all__ = ["INSTRUCTIONS", "RewriteResult", "build_request", "parse_reply", "rewrite_source"]

#: The role and the rules. Sent as the model's instructions, never as the
#: payload, so the payload stays a plain list of comments. The rules themselves
#: are deliberately not restated here: they are the skill's text, which travels
#: with the same request, and a paraphrase would be a second copy to keep true.
INSTRUCTIONS = """\
Ты редактор комментариев в конфигурации Nix. Режим — редактура; правила берутся \
из скилла humanizer-ru, который загружен в этот же запрос. Иди по его процедуре: \
жёсткие запреты, затем разметка от группы A к D, факт-замок (он важнее любого \
правила стиля) и сверка результата с исходником.

Жанр — документация: правь по группе A и грубым ошибкам, остальное не трогай. \
Комментарий, который и так читается, верни дословно, это нормальный результат.

Конструкции из таблицы запретов не должны появляться там, где их не было: \
внесённый запрет отменяет правку целиком, это проверяется машинно.

Пути, ссылки и числа сверяются с исходником так же машинно: правка, которая их \
теряет, меняет или добавляет, отменяется целиком.

Как отвечать:
строка [[N]] открывает комментарий с номером N;
текст нового комментария идёт следующей строкой и укладывается в одну строку;
номера идут по порядку, каждый ровно один раз, один абзац на комментарий;
никаких пояснений, кавычек вокруг текста и блоков кода; \
отчёт из скилла (баллы сканера, «что снято») здесь не нужен, ответ — только \
строки [[N]] с текстом.
"""

_SECTION = re.compile(r"^\[\[(\d+)\]\]$")

#: One round trip is the plan; the second is the retry for whatever failed.
_ATTEMPTS = 2


@dataclass(frozen=True)
class RewriteResult:
    """What the round trip did: rewritten bytes, applied ids, failures, skips."""

    rewritten: bytes
    applied: tuple[int, ...]
    unrewritten: tuple[tuple[int, str], ...]
    skipped: tuple[tuple[CommentSpan, str], ...]
    requests: int


def build_request(items, *, invocation: str | None = None) -> str:
    """Build the payload for ``(number, body)`` pairs the model is asked to rewrite.

    Only comment prose goes in: no code, no comment markers, no delimiters.

    ``invocation`` opens the payload — pi reads a ``/skill:`` line from the prompt
    text itself, so the rules cannot be requested from anywhere else. It is
    followed by a space and then the prose, not by a newline: pi takes the skill's
    name to be everything up to the first space in the prompt, and a name it cannot
    find makes it pass the prompt through untouched — the request would go out with
    no rules at all.
    """
    lines = ["Комментарии:"]
    for number, body in items:
        lines.append(f"[[{number}]]")
        lines.append(body.decode("utf-8", "surrogateescape"))
    payload = "\n".join(lines) + "\n"
    return f"{invocation} {payload}" if invocation else payload


def parse_reply(reply: str, numbers, *, multiline=()) -> dict[int, bytes]:
    """Read ``[[N]]`` sections out of ``reply``, keeping only ``numbers``.

    ``multiline`` names the numbers whose comment may span lines (a block
    comment). For every other number only the first line of the section is the
    comment, so a model that appends a remark does not smuggle it into the file.
    """
    wanted = set(numbers)
    multiline = set(multiline)
    bodies: dict[int, bytes] = {}
    seen: set[int] = set()
    duplicates: set[int] = set()
    current: int | None = None
    buffer: list[str] = []
    for line in reply.splitlines():
        match = _SECTION.match(line.strip())
        if match:
            _store(bodies, current, buffer, wanted, multiline)
            current = int(match.group(1))
            if current in seen:
                duplicates.add(current)
            seen.add(current)
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    _store(bodies, current, buffer, wanted, multiline)
    # A repeated id is ambiguous even when one section is empty, or both agree.
    # Leave it missing so only that comment is retried; valid neighbours survive.
    return {number: body for number, body in bodies.items() if number not in duplicates}


def rewrite_source(source: bytes, generate, *, invocation: str | None = None) -> RewriteResult:
    """Rewrite the eligible comments of ``source`` using ``generate(prompt) -> str``."""
    selection = select_candidates(source)
    candidates = selection.candidates
    if not candidates:
        return RewriteResult(source, (), (), selection.skipped, 0)

    numbered = list(enumerate(candidates, 1))
    approved: dict[int, bytes] = {}
    pending: list[tuple[int, Candidate, str]] = [(number, item, "") for number, item in numbered]
    requests = 0

    for _ in range(_ATTEMPTS):
        if not pending:
            break
        prompt = build_request(
            [(number, item.body) for number, item, _ in pending], invocation=invocation
        )
        reply = generate(prompt)
        requests += 1
        parsed = parse_reply(
            reply,
            [number for number, _, _ in pending],
            multiline={
                number for number, item, _ in pending if item.span.is_block
            },
        )
        still_pending: list[tuple[int, Candidate, str]] = []
        for number, item, _ in pending:
            body = parsed.get(number)
            if body is None:
                still_pending.append((number, item, "the reply had no [[%d]] section" % number))
                continue
            comment = item.assemble(body)
            try:
                apply_rewrites(source, [Rewrite(item.span, comment)])
            except (CommentError, TypeError) as error:
                still_pending.append((number, item, str(error)))
                continue
            drift = value_drift(
                item.body.decode("utf-8", "surrogateescape"),
                body.decode("utf-8", "surrogateescape"),
            )
            if drift:
                still_pending.append(
                    (number, item, "changes technical values: " + ", ".join(drift))
                )
                continue
            added = introduced_bans(
                item.span.text.decode("utf-8", "surrogateescape"),
                comment.decode("utf-8", "surrogateescape"),
            )
            if added:
                still_pending.append(
                    (number, item, "adds a hard ban: " + ", ".join(added))
                )
                continue
            approved[number] = comment
        pending = still_pending

    rewrites = [
        Rewrite(candidate.span, approved[number])
        for number, candidate in numbered
        if number in approved
    ]
    return RewriteResult(
        rewritten=apply_rewrites(source, rewrites),
        applied=tuple(number for number, _ in numbered if number in approved),
        unrewritten=tuple((number, reason) for number, _, reason in pending),
        skipped=selection.skipped,
        requests=requests,
    )


def _store(
    bodies, current: int | None, buffer: list[str], wanted: set[int], multiline: set[int]
) -> None:
    if current is None or current not in wanted:
        return
    lines = _unwrap_fence([line.rstrip() for line in buffer])
    if current in multiline:
        text = "\n".join(lines).strip()
    else:
        text = next((line.strip() for line in lines if line.strip()), "")
    if text:
        bodies[current] = text.encode("utf-8", "surrogateescape")


def _unwrap_fence(lines: list[str]) -> list[str]:
    """Drop a ``` wrapper if the model fenced the body."""
    filled = [index for index, line in enumerate(lines) if line.strip()]
    if not filled:
        return lines
    first, last = filled[0], filled[-1]
    if not lines[first].strip().startswith("```") or not lines[last].strip().startswith("```"):
        return lines
    return lines[first + 1 : last]
