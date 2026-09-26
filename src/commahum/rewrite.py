"""The rewriting round trip: ask once per file, apply only what survives.

Everything the model is allowed to influence is text. It receives numbered prose
bodies — no code, no `#`, no `TODO:` marker, no delimiters — and returns numbered
prose. The assembly and the splice stay on this side of the line, and every
returned body has to pass the patcher's rules before it lands in the file.

A body that fails is retried once on its own, and if it fails again the comment
keeps its original text. Nothing is ever forced in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .bans import introduced_bans
from .comments import CommentError, CommentSpan
from .patch import Rewrite, apply_rewrites
from .selection import Candidate, select_candidates

__all__ = ["INSTRUCTIONS", "RewriteResult", "build_request", "parse_reply", "rewrite_source"]

#: The role and the rules. Sent as the model's instructions, never as the
#: payload, so the payload stays a plain list of comments.
INSTRUCTIONS = """\
Ты редактор комментариев в конфигурации Nix. Работай по правилам humanizer-ru: \
убери из русского текста следы нейросети, канцелярит и воду, не сломав смысл, \
факты и голос автора.

Главное правило: удаляй, не дописывай. Маркер снимается удалением оболочки или \
перестройкой фразы из тех же слов. Эмоции, оценки, шутки, образы и частицы, \
которых нет в исходнике, не добавляются. Если комментарий и так читается, верни \
его без изменений.

Жёсткие запреты, снимаются всегда: длинное тире «—» и короткое тире «–» (запятая, \
двоеточие, точка или перестройка); «не просто X, а Y» и «не только X, но и Y» \
(прямое утверждение или перечисление); «в современном мире», «в условиях …»; \
«стоит отметить», «важно понимать», «важно помнить», «важно отметить»; \
«данный/данная/данное» → «этот/эта/это»; «является»; «играет важную роль», \
«играет ключевую роль»; «можно с уверенностью сказать»; «подводя итог», \
«таким образом» как вводный коннектор; «от X до Y» для несвязанных понятий; \
«погрузимся», «давайте посмотрим», «посмотрим поближе»; «и вот здесь начинается \
самое интересное»; «раскрыть потенциал», «вывести на новый уровень», \
«открывает новые горизонты/перспективы/возможности», «комплексный подход»; \
«в связи с этим». \
Канцелярит: отглагольные существительные («осуществление», «внедрение», \
«реализация», «обеспечение») заменяй глаголом.

Факт-замок, он важнее любого правила стиля: числа, единицы, имена, пути, \
названия, ссылки, условия и оговорки («может», «обычно», «примерно») \
переносятся дословно. Ничего не добавляй и ничего не удаляй: ни фактов, ни \
цифр, ни похвалы коду. Если мысль понятна только вместе с оговоркой, оговорка \
остаётся.

Как отвечать:
строка [[N]] открывает комментарий с номером N;
текст нового комментария идёт следующей строкой и укладывается в одну строку;
номера идут по порядку, один абзац на комментарий;
никаких пояснений, кавычек вокруг текста и блоков кода.
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


def build_request(items) -> str:
    """Build the payload for ``(number, body)`` pairs the model is asked to rewrite.

    Only comment prose goes in: no code, no comment markers, no delimiters.
    """
    lines = ["Комментарии:"]
    for number, body in items:
        lines.append(f"[[{number}]]")
        lines.append(body.decode("utf-8", "surrogateescape"))
    return "\n".join(lines) + "\n"


def parse_reply(reply: str, numbers, *, multiline=()) -> dict[int, bytes]:
    """Read ``[[N]]`` sections out of ``reply``, keeping only ``numbers``.

    ``multiline`` names the numbers whose comment may span lines (a block
    comment). For every other number only the first line of the section is the
    comment, so a model that appends a remark does not smuggle it into the file.
    """
    wanted = set(numbers)
    multiline = set(multiline)
    bodies: dict[int, bytes] = {}
    current: int | None = None
    buffer: list[str] = []
    for line in reply.splitlines():
        match = _SECTION.match(line.strip())
        if match:
            _store(bodies, current, buffer, wanted, multiline)
            current = int(match.group(1))
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    _store(bodies, current, buffer, wanted, multiline)
    return bodies


def rewrite_source(source: bytes, generate) -> RewriteResult:
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
        prompt = build_request([(number, item.body) for number, item, _ in pending])
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
