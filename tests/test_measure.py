"""The numbers that judge a change, and the rules for calling one a regression.

The scanner is a subprocess and the model is injected, so what is checked here is
the arithmetic: which differences count as a regression, which count as "these two
runs are not comparable", and that the JSON the scanner really writes is parsed in
the shape it really has.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from commahum.cli import main
from commahum.measure import compare, metrics_from, scan_notes

#: A payload in the shape `scan.py --json` really prints, penalties included.
PAYLOAD = {
    "words": 120,
    "score": {
        "score": 88,
        "band": "чисто",
        "penalties": [{"reason": "ровный ритм (CV=0.31, цель ≥0.45)", "points": -9}],
        "notes": ["стерильно: ни одного маркера"],
    },
    "genre": "marketing",
    "hard_bans": [],
    "hard_ban_count": 0,
    "markers": [["канцелярит", "осуществляется", 1]],
    "marker_count": 1,
    "before": {"source": "before.txt", "score": {"score": 61, "band": "правка"}},
    "facts": {"ok": True, "lost": [], "added": [], "kept": 16, "soft_added": [], "claims_added": []},
}

TOTALS = {"files": 2, "requests": 2, "applied": 7, "skipped": 1, "kept": 0}


class MetricsTests(unittest.TestCase):
    def test_the_scanner_payload_folds_into_the_recorded_numbers(self) -> None:
        metrics = metrics_from(PAYLOAD, TOTALS, model="deepseek/deepseek-flash")

        self.assertEqual(
            metrics,
            {
                "files": 2,
                "requests": 2,
                "applied": 7,
                "skipped": 1,
                "kept": 0,
                "score_before": 61,
                "score_after": 88,
                "bans": 0,
                "markers": 1,
                "facts_kept": 16,
                "facts_new": 0,
                "facts_lost": 0,
                "model": "deepseek/deepseek-flash",
            },
        )

    def test_a_payload_without_the_fact_lock_still_folds(self) -> None:
        """`--before` was not passed: there is nothing to compare against."""
        metrics = metrics_from({"score": {"score": 70}}, TOTALS)

        self.assertIsNone(metrics["score_before"])
        self.assertEqual(metrics["facts_new"], 0)

    def test_the_scanner_notes_are_printed_in_its_own_shape(self) -> None:
        notes = scan_notes(PAYLOAD)

        self.assertIn("-9 ровный ритм", notes)
        self.assertIn("стерильно", notes)

    def test_a_payload_without_penalties_says_so(self) -> None:
        self.assertEqual(scan_notes({"score": {}}), "  без штрафов")


class ComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = metrics_from(PAYLOAD, TOTALS, model="m")

    def test_an_identical_run_is_not_a_regression(self) -> None:
        regressions, complaints = compare(self.baseline, self.baseline)

        self.assertEqual((regressions, complaints), ([], []))

    def test_a_lower_score_is_a_regression(self) -> None:
        now = {**self.baseline, "score_after": 80}

        regressions, _ = compare(now, self.baseline)

        self.assertEqual(len(regressions), 1)
        self.assertIn("the score fell", regressions[0])

    def test_a_better_score_is_not_a_regression(self) -> None:
        regressions, _ = compare({**self.baseline, "score_after": 99}, self.baseline)

        self.assertEqual(regressions, [])

    def test_a_ban_that_appeared_is_a_regression(self) -> None:
        regressions, _ = compare({**self.baseline, "bans": 2}, self.baseline)

        self.assertEqual(len(regressions), 1)
        self.assertIn("hard bans appeared", regressions[0])

    def test_an_invented_fact_is_a_regression(self) -> None:
        regressions, _ = compare({**self.baseline, "facts_new": 1}, self.baseline)

        self.assertEqual(len(regressions), 1)
        self.assertIn("invented facts", regressions[0])

    def test_a_lost_fact_is_a_regression(self) -> None:
        regressions, _ = compare({**self.baseline, "facts_lost": 3}, self.baseline)

        self.assertEqual(len(regressions), 1)
        self.assertIn("dropped facts", regressions[0])

    def test_a_different_corpus_is_not_comparable(self) -> None:
        _, complaints = compare({**self.baseline, "files": 9}, self.baseline)

        self.assertEqual(len(complaints), 1)
        self.assertIn("not comparable", complaints[0])

    def test_a_different_model_is_not_comparable(self) -> None:
        _, complaints = compare({**self.baseline, "model": "other"}, self.baseline)

        self.assertEqual(len(complaints), 1)
        self.assertIn("other", complaints[0])


SOURCE = "{ ... }:\n# Осуществляется настройка сети хоста.\n{\n  networking.hostName = \"deep-thought\";\n}\n"


def extra_installed() -> bool:
    try:
        import pymorphy3  # noqa: F401
        import razdel  # noqa: F401
    except ImportError:
        return False
    return True


class FakeGenerator:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


@unittest.skipUnless(extra_installed(), "the `style` extra is not installed, so the scanner cannot run")
class MeasureCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        (self.corpus / "a.nix").write_text(SOURCE, encoding="utf-8")
        self.baseline = self.root / "baseline.json"

    def measure(self, *argv: str, reply: str = "[[1]]\nСеть настроена.\n") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["measure", *argv], generate=FakeGenerator(reply))
        return code, out.getvalue(), err.getvalue()

    def test_a_run_prints_the_numbers_and_writes_no_baseline_by_itself(self) -> None:
        code, out, err = self.measure(str(self.corpus))

        self.assertEqual(code, 0, err)
        self.assertIn("score_after:", out)
        self.assertIn("facts_lost: 0", out)
        self.assertFalse(self.baseline.exists())

    def test_saving_then_comparing_the_same_corpus_finds_no_regression(self) -> None:
        code, _, err = self.measure("--save", str(self.baseline), str(self.corpus))
        self.assertEqual(code, 0, err)

        code, out, err = self.measure("--baseline", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 0, err)
        self.assertIn("no regressions", out)

    def test_a_baseline_with_a_higher_score_is_a_regression(self) -> None:
        self.measure("--save", str(self.baseline), str(self.corpus))
        recorded = json.loads(self.baseline.read_text(encoding="utf-8"))
        recorded["score_after"] = recorded["score_after"] + 10
        self.baseline.write_text(json.dumps(recorded), encoding="utf-8")

        code, out, err = self.measure("--baseline", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 1, err)
        self.assertIn("the score fell", out)

    def test_a_baseline_from_another_model_is_not_comparable(self) -> None:
        self.measure("--save", str(self.baseline), str(self.corpus))
        recorded = json.loads(self.baseline.read_text(encoding="utf-8"))
        recorded["model"] = "some/other-model"
        self.baseline.write_text(json.dumps(recorded), encoding="utf-8")

        code, out, _ = self.measure("--baseline", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 2)
        self.assertIn("not comparable", out)

    def test_a_missing_baseline_is_reported_rather_than_ignored(self) -> None:
        code, _, err = self.measure("--baseline", str(self.root / "нет.json"), str(self.corpus))

        self.assertEqual(code, 2)
        self.assertIn("no baseline", err)

    def test_the_corpus_is_never_written_to(self) -> None:
        path = self.corpus / "a.nix"
        before = path.read_bytes()

        self.measure(str(self.corpus))

        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
