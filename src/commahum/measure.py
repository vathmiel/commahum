"""The `measure` command: the pipeline over a corpus, and the numbers that judge it.

Every other way of judging a change here ends in an opinion. A diff of fifty
comments can look better or worse and there is no arguing with the impression it
leaves; "the model seems to follow the flow" is a claim about a language model,
which is exactly the kind of claim this project has already been wrong about. So
this command does what the skill's own audit does: it runs the pipeline over the
files given — writing nothing, always — hands the prose before and after to the
vendored scanner in its `--json` mode, and prints the numbers the scanner returns.

The numbers are a small, stable shape: score before and after, hard bans in the
result, markers, and the fact lock (kept, lost, invented). `--save` writes them as
a baseline; `--baseline` compares a run against one and treats a lower score, a ban
that was not there, a lost fact or an invented one as a regression, exiting 1. A
baseline recorded over different files or against a different model is not
comparable, and saying so is exit 2 rather than a quiet pass.

The corpus in `eval/` is frozen for this purpose:

    commahum measure --model deepseek/deepseek-flash eval/corpus
    commahum measure --model deepseek/deepseek-flash --baseline eval/baseline.json eval/corpus

Nothing here is written to the files that were measured: the point is to know, not
to change. `--keep` puts the scanned before/after texts somewhere to read.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from . import asking
from .paths import collect
from .rewrite import rewrite_source
from .selection import prose
from .skill import vendored_skill

__all__ = ["add_arguments", "compare", "metrics_from", "run"]

#: The metrics a baseline records. Kept small on purpose: each one is a claim that
#: can fail, and a field nobody reads is a field nobody notices going wrong.
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


def metrics_from(payload: dict, totals: dict, *, model: str | None = None) -> dict:
    """The scanner's JSON, folded into the handful of numbers a baseline keeps."""
    score = payload.get("score") or {}
    facts = payload.get("facts") or {}
    return {
        "files": totals["files"],
        "requests": totals["requests"],
        "applied": totals["applied"],
        "skipped": totals["skipped"],
        "kept": totals["kept"],
        "score_before": (payload.get("before") or {}).get("score", {}).get("score"),
        "score_after": score.get("score"),
        "bans": payload.get("hard_ban_count"),
        "markers": payload.get("marker_count"),
        "facts_kept": facts.get("kept"),
        "facts_new": len(facts.get("added") or []),
        "facts_lost": len(facts.get("lost") or []),
        "model": model,
    }


def compare(now: dict, baseline: dict) -> tuple[list[str], list[str]]:
    """Return ``(regressions, complaints)`` — complaints are about comparability."""
    complaints: list[str] = []
    if now.get("files") != baseline.get("files"):
        complaints.append(
            f"the baseline measured {baseline.get('files')} files, this run {now.get('files')}: "
            "the corpus changed, so the numbers are not comparable"
        )
    if baseline.get("model") and now.get("model") != baseline.get("model"):
        complaints.append(
            f"the baseline was recorded with {baseline.get('model')}, this run used {now.get('model')}"
        )

    regressions: list[str] = []
    if _number(now, "score_after") < _number(baseline, "score_after"):
        regressions.append(
            f"the score fell: {baseline.get('score_after')} → {now.get('score_after')}"
        )
    if _number(now, "bans") > _number(baseline, "bans"):
        regressions.append(f"hard bans appeared: {baseline.get('bans')} → {now.get('bans')}")
    if _number(now, "facts_new") > _number(baseline, "facts_new"):
        regressions.append(
            f"the rewrite invented facts: {baseline.get('facts_new')} → {now.get('facts_new')}"
        )
    if _number(now, "facts_lost") > _number(baseline, "facts_lost"):
        regressions.append(
            f"the rewrite dropped facts: {baseline.get('facts_lost')} → {now.get('facts_lost')}"
        )
    return regressions, complaints


def _number(values: dict, field: str) -> float:
    value = values.get(field)
    return float(value) if isinstance(value, (int, float)) else 0.0


def run(arguments, generate=None) -> int:
    skill = vendored_skill()
    if generate is None:
        generate = asking.generator(arguments, skill=skill)

    totals = {"files": 0, "requests": 0, "applied": 0, "skipped": 0, "kept": 0}
    before_parts: list[str] = []
    after_parts: list[str] = []
    for path in collect(arguments.paths):
        source = path.read_bytes()
        result = rewrite_source(source, generate, invocation=skill.invocation)
        before_parts.append(f"# {path}\n{prose(source)}")
        after_parts.append(f"# {path}\n{prose(result.rewritten)}")
        totals["files"] += 1
        totals["requests"] += result.requests
        totals["applied"] += len(result.applied)
        totals["skipped"] += len(result.skipped)
        totals["kept"] += len(result.unrewritten)
        print(
            f"{path}: {len(result.applied)} rewritten, {len(result.skipped)} skipped, "
            f"{len(result.unrewritten)} kept, {result.requests} requests",
            file=sys.stderr,
        )

    if not totals["files"]:
        print("commahum: nothing to measure", file=sys.stderr)
        return 2

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

    model = getattr(generate, "description", None) or arguments.model
    metrics = metrics_from(payload, totals, model=model)
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
