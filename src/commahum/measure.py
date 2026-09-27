"""The `measure` command: the pipeline over a corpus, and the numbers that judge it.

Every other way of judging a change here ends in an opinion. A diff of fifty
comments can look better or worse and there is no arguing with the impression it
leaves; "the model seems to follow the flow" is a claim about a language model,
which is exactly the kind of claim this project has already been wrong about. So
this command does what the skill's own audit does: it runs the pipeline over the
files given — writing nothing, always — hands the prose before and after to the
vendored scanner in its `--json` mode, and prints the numbers the scanner returns.

The numbers are a small, stable shape: score before and after, hard bans in the
result, markers, and the fact lock (kept, lost, invented). They are recorded once
for the corpus and once for every file in it, beside the file's path and the SHA256
of the bytes that were measured, because a file count is not an identity: a file can
be renamed or edited underneath a baseline and still count the same. `--save` writes
all of it as a baseline; `--baseline` compares a run against one and treats a lower
score, a ban that was not there, a lost fact or an invented one as a regression —
for the corpus or for a single file — exiting 1. A baseline recorded over a different
corpus (a file gained, lost, renamed or changed) or against a different model is not
comparable, and saying so is exit 2 rather than a quiet pass; a baseline from before
the corpus was recorded is not comparable either, and only a deliberate `--save`
migrates it, which is the moment to say why the numbers may move.

The corpus in `eval/` is frozen for this purpose:

    commahum measure --model deepseek/deepseek-flash eval/corpus
    commahum measure --model deepseek/deepseek-flash --baseline eval/baseline.json eval/corpus

Nothing here is written to the files that were measured: the point is to know, not
to change. `--keep` puts the scanned before/after texts somewhere to read.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import asking
from .paths import collect, display_name
from .rewrite import rewrite_source
from .selection import prose
from .skill import vendored_skill

__all__ = ["add_arguments", "compare", "metrics_from", "run"]

#: The aggregate metrics a baseline records, printed one per line. Kept small on
#: purpose: each one is a claim that can fail, and a field nobody reads is a field
#: nobody notices going wrong. The per-file record (`corpus`) rides alongside them.
FIELDS = (
    "files",
    "requests",
    "applied",
    "skipped",
    "kept",
    "score_before",
    "score_after",
    "bans",
    "markers",
    "facts_kept",
    "facts_new",
    "facts_lost",
    "model",
)


def add_arguments(parser) -> None:
    parser.add_argument("paths", nargs="+", help="the corpus: files, or directories to walk for *.nix")
    asking.add_options(parser, catalog=True)
    parser.add_argument("--genre", default=None, help="pass a genre through to the scanner")
    parser.add_argument("--save", default=None, help="write the metrics to this JSON file")
    parser.add_argument(
        "--baseline",
        default=None,
        help="compare against metrics saved earlier; exit 1 on a regression, 2 when not comparable",
    )
    parser.add_argument(
        "--keep",
        default=None,
        help="write the scanned before/after texts here instead of a temporary directory",
    )


def metrics_from(payload: dict, totals: dict, *, model: str | None = None, corpus=()) -> dict:
    """The scanner's JSON, folded into the handful of numbers a baseline keeps.

    ``corpus`` is the per-file record — the path and SHA256 of the bytes that were
    measured, and what happened to each file — so a baseline can say which corpus it
    is a baseline *of*, not only how many files were in it. It is kept out of the
    metrics when empty, because a caller folding a bare payload has no corpus to name.
    """
    metrics = {
        "files": totals["files"],
        "requests": totals["requests"],
        "applied": totals["applied"],
        "skipped": totals["skipped"],
        "kept": totals["kept"],
    }
    metrics.update(scanner_numbers(payload))
    metrics["model"] = model
    if corpus:
        metrics["corpus"] = [dict(record) for record in corpus]
    return metrics


def scanner_numbers(payload: dict) -> dict:
    """The scanner's JSON folded into the numbers a baseline keeps, whole corpus or one file."""
    score = payload.get("score") or {}
    facts = payload.get("facts") or {}
    return {
        "score_before": (payload.get("before") or {}).get("score", {}).get("score"),
        "score_after": score.get("score"),
        "bans": payload.get("hard_ban_count"),
        "markers": payload.get("marker_count"),
        "facts_kept": facts.get("kept"),
        "facts_new": len(facts.get("added") or []),
        "facts_lost": len(facts.get("lost") or []),
    }


def compare(now: dict, baseline: dict) -> tuple[list[str], list[str]]:
    """Return ``(regressions, complaints)`` — complaints are about comparability."""
    complaints: list[str] = []
    recorded = _corpus_by_path(baseline)
    measured = _corpus_by_path(now)
    baseline_issue = _corpus_issue(baseline)
    measured_issue = _corpus_issue(now)
    if baseline_issue:
        complaints.append(f"the baseline has {baseline_issue}; re-record it deliberately with --save")
    if measured_issue:
        complaints.append(f"this run has {measured_issue}")
    if not baseline_issue and not measured_issue:
        if recorded and measured:
            complaints.extend(_corpus_complaints(recorded, measured))
        else:
            complaints.extend(_identity_complaints(recorded, measured))
    if now.get("files") != baseline.get("files"):
        complaints.append(
            f"the baseline measured {baseline.get('files')} files, this run {now.get('files')}: "
            "the corpus changed, so the numbers are not comparable"
        )
    if baseline.get("model") and now.get("model") != baseline.get("model"):
        complaints.append(
            f"the baseline was recorded with {baseline.get('model')}, this run used {now.get('model')}"
        )

    regressions: list[str] = _number_regressions(now, baseline)
    if not baseline_issue and not measured_issue and recorded and measured:
        regressions.extend(_per_file_regressions(recorded, measured))
    return regressions, complaints


def _identity_complaints(recorded: dict, measured: dict) -> list[str]:
    """Whether the two runs can be shown to have measured the same files at all."""
    if not recorded:
        return [
            "the baseline records no per-file identity (`corpus`: the path and SHA256 of every "
            "file it measured), so it cannot be compared with this run; re-record it deliberately "
            "with --save, which is the moment to say why the numbers may move"
        ]
    return ["this run records no per-file identity, so it cannot be compared with the baseline"]


def _corpus_complaints(recorded: dict, measured: dict) -> list[str]:
    """Every way the corpus itself can differ: a file gained, lost, or changed underneath."""
    complaints: list[str] = []
    for name in sorted(recorded.keys() - measured.keys()):
        complaints.append(
            f"the baseline measured {name}, which this run did not: the corpus lost a file"
        )
    for name in sorted(measured.keys() - recorded.keys()):
        complaints.append(
            f"this run measured {name}, which the baseline did not: the corpus gained a file"
        )
    for name in sorted(recorded.keys() & measured.keys()):
        if recorded[name].get("sha256") != measured[name].get("sha256"):
            complaints.append(
                f"the contents of {name} changed since the baseline was recorded "
                f"({_short(recorded[name].get('sha256'))} → {_short(measured[name].get('sha256'))})"
            )
    return complaints


def _per_file_regressions(recorded: dict, measured: dict) -> list[str]:
    """The aggregate rules again, once per file, so one bad file is not averaged away."""
    regressions: list[str] = []
    for name in sorted(recorded.keys() & measured.keys()):
        old, new = recorded[name], measured[name]
        if old.get("sha256") != new.get("sha256"):
            # Not the same bytes: `_corpus_complaints` already refuses to compare, and
            # the numbers would be about two different files.
            continue
        regressions.extend(_number_regressions(new, old, subject=f"{name}: "))
    return regressions


def _number_regressions(now: dict, baseline: dict, subject: str = "") -> list[str]:
    """The rules a set of numbers has to break to be called a regression."""
    regressions: list[str] = []
    if _number(now, "score_after") < _number(baseline, "score_after"):
        regressions.append(
            f"{subject}the score fell: {baseline.get('score_after')} → {now.get('score_after')}"
        )
    if _number(now, "bans") > _number(baseline, "bans"):
        regressions.append(f"{subject}hard bans appeared: {baseline.get('bans')} → {now.get('bans')}")
    if _number(now, "facts_new") > _number(baseline, "facts_new"):
        regressions.append(
            f"{subject}the rewrite invented facts: {baseline.get('facts_new')} → {now.get('facts_new')}"
        )
    if _number(now, "facts_lost") > _number(baseline, "facts_lost"):
        regressions.append(
            f"{subject}the rewrite dropped facts: {baseline.get('facts_lost')} → {now.get('facts_lost')}"
        )
    return regressions


def _corpus_issue(metrics: dict) -> str | None:
    """Reject partial records rather than silently skipping per-file regressions."""
    records = metrics.get("corpus")
    if records is None:
        return None  # Legacy baselines get the dedicated no-identity complaint.
    if not isinstance(records, list) or not records:
        return "no usable per-file identity"
    seen: set[str] = set()
    numbers = (
        "requests", "applied", "skipped", "kept", "score_before", "score_after",
        "bans", "markers", "facts_kept", "facts_new", "facts_lost",
    )
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not record["path"]:
            return "an incomplete per-file identity"
        path = record["path"]
        if path in seen:
            return f"a duplicate per-file path: {path}"
        seen.add(path)
        digest = record.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return f"an invalid SHA256 for {path}"
        if any(type(record.get(field)) not in (int, float) for field in numbers):
            return f"incomplete per-file numbers for {path}"
    return None


def _corpus_by_path(metrics: dict) -> dict[str, dict]:
    """The per-file records a metrics dict carries, keyed by path.

    Empty when there is no usable identity: no `corpus` at all, or records without a
    path or a SHA256 — a partial identity would let a changed file pass as the same one.
    """
    records = metrics.get("corpus")
    if not isinstance(records, list) or not records:
        return {}
    by_path: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            return {}
        path, digest = record.get("path"), record.get("sha256")
        if not isinstance(path, str) or not path or not isinstance(digest, str) or not digest:
            return {}
        by_path[path] = record
    return by_path


def _short(digest) -> str:
    return f"{digest[:12]}…" if isinstance(digest, str) else "no hash"


def _number(values: dict, field: str) -> float:
    value = values.get(field)
    return float(value) if isinstance(value, (int, float)) else 0.0


def run(arguments, generate=None) -> int:
    skill = vendored_skill()
    if generate is None:
        generate = asking.generator(arguments, skill=skill)

    records: list[dict] = []
    before_parts: list[str] = []
    after_parts: list[str] = []
    for path in collect(arguments.paths):
        source = path.read_bytes()
        result = rewrite_source(source, generate, invocation=skill.invocation)
        before_parts.append(f"# {path}\n{prose(source)}")
        after_parts.append(f"# {path}\n{prose(result.rewritten)}")
        records.append(
            {
                # The identity a baseline needs to be a baseline *of* this corpus: the
                # path it was reached by and the hash of the bytes that were measured.
                "path": display_name(path),
                "sha256": hashlib.sha256(source).hexdigest(),
                "requests": result.requests,
                "applied": len(result.applied),
                "skipped": len(result.skipped),
                "kept": len(result.unrewritten),
            }
        )
        print(
            f"{path}: {len(result.applied)} rewritten, {len(result.skipped)} skipped, "
            f"{len(result.unrewritten)} kept, {result.requests} requests",
            file=sys.stderr,
        )

    if not records:
        print("commahum: nothing to measure", file=sys.stderr)
        return 2

    totals = {
        "files": len(records),
        "requests": sum(record["requests"] for record in records),
        "applied": sum(record["applied"] for record in records),
        "skipped": sum(record["skipped"] for record in records),
        "kept": sum(record["kept"] for record in records),
    }

    scratch = arguments.keep
    with tempfile.TemporaryDirectory() if scratch is None else _kept(scratch) as directory:
        before_file = Path(directory) / "before.txt"
        after_file = Path(directory) / "after.txt"
        before_file.write_text("\n\n".join(before_parts), encoding="utf-8")
        after_file.write_text("\n\n".join(after_parts), encoding="utf-8")
        if scratch is not None:
            print(f"scanned: {after_file} against {before_file}", file=sys.stderr)
        payload, status = _scan(skill.directory, before_file, after_file, arguments.genre)
        if payload is None:
            print(f"commahum: the scanner did not return metrics (exit {status})", file=sys.stderr)
            return 2
        # The same scan again for each file on its own: the corpus score cannot say
        # whether one file in it got worse while another got better.
        for index, (record, part_before, part_after) in enumerate(
            zip(records, before_parts, after_parts)
        ):
            file_before = Path(directory) / f"before-{index}.txt"
            file_after = Path(directory) / f"after-{index}.txt"
            file_before.write_text(part_before, encoding="utf-8")
            file_after.write_text(part_after, encoding="utf-8")
            file_payload, file_status = _scan(
                skill.directory, file_before, file_after, arguments.genre
            )
            if file_payload is None:
                print(
                    f"commahum: the scanner did not return metrics for {record['path']} "
                    f"(exit {file_status})",
                    file=sys.stderr,
                )
                return 2
            record.update(scanner_numbers(file_payload))

    model = getattr(generate, "description", None) or arguments.model
    metrics = metrics_from(payload, totals, model=model, corpus=records)
    for field in FIELDS:
        print(f"{field}: {metrics[field]}")
    print(scan_notes(payload))

    if arguments.save:
        Path(arguments.save).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline saved: {arguments.save}", file=sys.stderr)

    if not arguments.baseline:
        return 0

    baseline_path = Path(arguments.baseline)
    if not baseline_path.is_file():
        print(f"commahum: no baseline at {baseline_path}", file=sys.stderr)
        return 2
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    regressions, complaints = compare(metrics, baseline)
    for complaint in complaints:
        print(f"⚠ {complaint}")
    for regression in regressions:
        print(f"✗ {regression}")
    if complaints:
        print(f"\nnot comparable with {baseline_path}: {len(complaints)} reasons above")
        return 2
    if regressions:
        print(f"\n{len(regressions)} regressions against {baseline_path}")
        return 1
    print(f"\nno regressions against {baseline_path}")
    return 0


def scan_notes(payload: dict) -> str:
    """The scanner's own words about the result, so the numbers are not the whole story."""
    score = payload.get("score") or {}
    notes = list(score.get("notes") or [])
    for penalty in score.get("penalties") or []:
        # As the scanner writes it: {"reason": ..., "points": ...}. A pair is
        # tolerated because the shape is the scanner's to change, not ours.
        if isinstance(penalty, dict):
            reason, points = penalty.get("reason"), penalty.get("points")
        else:
            reason, points = penalty
        notes.append(f"{points:+d} {reason}" if isinstance(points, int) else f"{points} {reason}")
    return "\n".join(f"  {note}" for note in notes) if notes else "  без штрафов"


def _scan(skill_directory: Path, before_file: Path, after_file: Path, genre: str | None):
    """Run the vendored scanner in its `--json` mode; return (payload, exit status).

    `-B`, because the scanner imports the skill's own package and bytecode caches
    would land inside the vendored tree — where the pin tests would then find files
    the upstream revision does not have.
    """
    command = [
        sys.executable,
        "-B",
        str(skill_directory / "scripts" / "scan.py"),
        str(after_file),
        "--json",
        "--before",
        str(before_file),
    ]
    if genre:
        command += ["--genre", genre]
    scan = subprocess.run(command, text=True, capture_output=True)
    try:
        return json.loads(scan.stdout), scan.returncode
    except json.JSONDecodeError:
        print(scan.stdout[-2000:], file=sys.stderr)
        print(scan.stderr[-2000:], file=sys.stderr)
        return None, scan.returncode


class _kept:
    """A directory that is created and handed over, but not cleaned up."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def __enter__(self) -> str:
        self.path.mkdir(parents=True, exist_ok=True)
        return str(self.path)

    def __exit__(self, *_: object) -> bool:
        return False
