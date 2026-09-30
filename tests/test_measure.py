"""The numbers that judge a change, and the rules for calling one a regression.

The scanner is a subprocess and the model is injected, so what is checked here is
the arithmetic: which differences count as a regression, which count as "these two
runs are not comparable", and that the JSON the scanner really writes is parsed in
the shape it really has.
"""

from __future__ import annotations

import contextlib
import hashlib
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


def corpus_records() -> list[dict]:
    """Two files in the shape `measure` records them: identity first, then the numbers."""
    return [
        {
            "path": "eval/corpus/cancelerit.nix",
            "sha256": "aa" * 32,
            "requests": 1,
            "applied": 3,
            "unchanged": 0,
            "skipped": 0,
            "kept": 1,
            "score_before": 41,
            "score_after": 88,
            "bans": 0,
            "markers": 1,
            "facts_kept": 4,
            "facts_new": 0,
            "facts_lost": 0,
        },
        {
            "path": "eval/corpus/facts.nix",
            "sha256": "bb" * 32,
            "requests": 1,
            "applied": 2,
            "unchanged": 0,
            "skipped": 1,
            "kept": 0,
            "score_before": 55,
            "score_after": 91,
            "bans": 0,
            "markers": 0,
            "facts_kept": 9,
            "facts_new": 0,
            "facts_lost": 0,
        },
    ]


class MetricsTests(unittest.TestCase):
    def test_the_scanner_payload_folds_into_the_recorded_numbers(self) -> None:
        metrics = metrics_from(PAYLOAD, TOTALS, model="deepseek/deepseek-flash")

        self.assertEqual(
            metrics,
            {
                "rewrite_counts_version": 2,
                "files": 2,
                "requests": 2,
                "applied": 7,
                "unchanged": 0,
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

    def test_the_corpus_is_recorded_file_by_file_with_its_hashes(self) -> None:
        metrics = metrics_from(PAYLOAD, TOTALS, model="m", corpus=corpus_records())

        self.assertEqual(metrics["corpus"], corpus_records())
        self.assertEqual(metrics["files"], 2)

    def test_the_scanner_notes_are_printed_in_its_own_shape(self) -> None:
        notes = scan_notes(PAYLOAD)

        self.assertIn("-9 ровный ритм", notes)
        self.assertIn("стерильно", notes)

    def test_a_payload_without_penalties_says_so(self) -> None:
        self.assertEqual(scan_notes({"score": {}}), "  без штрафов")


class ComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = self.measured()

    def measured(self, records: list[dict] | None = None) -> dict:
        """The metrics a run over the corpus `corpus_records()` describes would write down."""
        return metrics_from(
            PAYLOAD, TOTALS, model="m", corpus=corpus_records() if records is None else records
        )

    def test_an_identical_run_is_not_a_regression(self) -> None:
        regressions, complaints = compare(self.baseline, self.baseline)

        self.assertEqual((regressions, complaints), ([], []))

    def test_accepted_reply_counts_are_not_compared_with_actual_change_counts(self) -> None:
        legacy = dict(self.baseline)
        del legacy["rewrite_counts_version"]

        _, complaints = compare(self.baseline, legacy)

        self.assertTrue(any("accepted replies" in complaint for complaint in complaints))
        self.assertTrue(any("--save" in complaint for complaint in complaints))

    def test_a_baseline_without_per_file_identity_is_not_comparable(self) -> None:
        """An older baseline knows only a count: it cannot say *which* files it measured."""
        legacy = {key: value for key, value in self.baseline.items() if key != "corpus"}

        regressions, complaints = compare(self.baseline, legacy)

        self.assertEqual(regressions, [])
        self.assertEqual(len(complaints), 1)
        self.assertIn("no per-file identity", complaints[0])
        self.assertIn("--save", complaints[0])

    def test_a_run_that_recorded_no_identity_is_not_comparable(self) -> None:
        no_identity = {key: value for key, value in self.baseline.items() if key != "corpus"}

        regressions, complaints = compare(no_identity, self.baseline)

        self.assertEqual(regressions, [])
        self.assertEqual(len(complaints), 1)
        self.assertIn("this run records no per-file identity", complaints[0])

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

    def test_a_renamed_file_is_not_comparable(self) -> None:
        """Same bytes, same count, a different name: the baseline was of another corpus."""
        records = corpus_records()
        records[1]["path"] = "eval/corpus/факты.nix"

        regressions, complaints = compare(self.measured(records), self.baseline)

        self.assertEqual(regressions, [])
        self.assertEqual(len(complaints), 2)
        self.assertTrue(any("eval/corpus/facts.nix" in complaint for complaint in complaints))
        self.assertTrue(any("eval/corpus/факты.nix" in complaint for complaint in complaints))

    def test_a_changed_file_is_not_comparable_at_the_same_file_count(self) -> None:
        records = corpus_records()
        records[0]["sha256"] = "cc" * 32

        regressions, complaints = compare(self.measured(records), self.baseline)

        self.assertEqual(regressions, [])
        self.assertEqual(len(complaints), 1)
        self.assertIn("eval/corpus/cancelerit.nix", complaints[0])
        self.assertIn("changed since the baseline was recorded", complaints[0])

    def test_a_lower_per_file_score_is_a_regression_naming_the_file(self) -> None:
        records = corpus_records()
        records[0]["score_after"] = 70

        regressions, complaints = compare(self.measured(records), self.baseline)

        self.assertEqual(complaints, [])
        self.assertEqual(len(regressions), 1)
        self.assertIn("eval/corpus/cancelerit.nix", regressions[0])
        self.assertIn("the score fell", regressions[0])

    def test_a_file_that_lost_a_fact_is_a_regression(self) -> None:
        records = corpus_records()
        records[1]["facts_lost"] = 2

        regressions, _ = compare(self.measured(records), self.baseline)

        self.assertEqual(len(regressions), 1)
        self.assertIn("eval/corpus/facts.nix", regressions[0])
        self.assertIn("dropped facts", regressions[0])

    def test_a_file_that_gained_a_ban_is_a_regression(self) -> None:
        records = corpus_records()
        records[0]["bans"] = 1

        regressions, _ = compare(self.measured(records), self.baseline)

        self.assertEqual(len(regressions), 1)
        self.assertIn("hard bans appeared", regressions[0])

    def test_a_better_per_file_score_is_not_a_regression(self) -> None:
        records = corpus_records()
        records[0]["score_after"] = 99

        regressions, complaints = compare(self.measured(records), self.baseline)

        self.assertEqual((regressions, complaints), ([], []))

    def test_a_baseline_with_identity_but_no_per_file_numbers_is_not_comparable(self) -> None:
        """A partial migration must not silently disable per-file regression checks."""
        baseline = {
            **self.baseline,
            "corpus": [
                {key: record[key] for key in ("path", "sha256")} for record in corpus_records()
            ],
        }

        regressions, complaints = compare(self.baseline, baseline)

        self.assertEqual(regressions, [])
        self.assertTrue(any("per-file" in complaint for complaint in complaints))

    def test_duplicate_paths_in_a_baseline_are_not_comparable(self) -> None:
        records = corpus_records()
        records[1]["path"] = records[0]["path"]

        _, complaints = compare(self.baseline, self.measured(records))

        self.assertTrue(any("duplicate" in complaint for complaint in complaints), complaints)

    def test_invalid_sha256_in_a_baseline_is_not_comparable(self) -> None:
        records = corpus_records()
        records[0]["sha256"] = "not-a-hash"

        _, complaints = compare(self.baseline, self.measured(records))

        self.assertTrue(any("SHA256" in complaint for complaint in complaints), complaints)

    def test_a_changed_file_is_not_judged_by_its_own_numbers(self) -> None:
        """Changed bytes are a comparability problem; a fall in them is not called a regression."""
        records = corpus_records()
        records[0]["sha256"] = "cc" * 32
        records[0]["score_after"] = 10

        regressions, complaints = compare(self.measured(records), self.baseline)

        self.assertEqual(regressions, [])
        self.assertTrue(complaints)

    def test_a_different_model_is_not_comparable(self) -> None:
        _, complaints = compare({**self.baseline, "model": "other"}, self.baseline)

        self.assertEqual(len(complaints), 1)
        self.assertIn("other", complaints[0])


SOURCE = "{ ... }:\n# Осуществляется настройка сети хоста.\n{\n  networking.hostName = \"deep-thought\";\n}\n"


class FrozenBaselineTests(unittest.TestCase):
    """`eval/baseline.json` is the baseline of record: it has to name the corpus it measured."""

    def test_the_frozen_baseline_names_the_frozen_corpus_it_measured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        baseline = json.loads((root / "eval" / "baseline.json").read_text(encoding="utf-8"))
        files = sorted((root / "eval" / "corpus").rglob("*.nix"))

        recorded = {record["path"]: record["sha256"] for record in baseline["corpus"]}

        self.assertEqual(
            recorded,
            {
                str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in files
            },
        )
        self.assertEqual(baseline["files"], len(files))


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

    def test_an_unchanged_reply_is_recorded_as_unchanged_not_applied(self) -> None:
        code, _, err = self.measure(
            "--save", str(self.baseline), str(self.corpus),
            reply="[[1]]\nОсуществляется настройка сети хоста.\n",
        )

        self.assertEqual(code, 0, err)
        recorded = json.loads(self.baseline.read_text(encoding="utf-8"))
        self.assertEqual((recorded["applied"], recorded["unchanged"]), (0, 1))
        self.assertEqual((recorded["corpus"][0]["applied"], recorded["corpus"][0]["unchanged"]), (0, 1))

    def test_a_saved_baseline_records_every_file_by_path_and_hash(self) -> None:
        code, _, err = self.measure("--save", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 0, err)
        recorded = json.loads(self.baseline.read_text(encoding="utf-8"))
        source = (self.corpus / "a.nix").read_bytes()
        (record,) = recorded["corpus"]
        self.assertTrue(record["path"].endswith("corpus/a.nix"), record["path"])
        self.assertEqual(record["sha256"], hashlib.sha256(source).hexdigest())
        self.assertEqual(recorded["files"], 1)

    def test_a_saved_baseline_records_each_files_own_numbers(self) -> None:
        self.measure("--save", str(self.baseline), str(self.corpus))

        recorded = json.loads(self.baseline.read_text(encoding="utf-8"))
        (record,) = recorded["corpus"]

        self.assertEqual((record["applied"], record["skipped"], record["kept"]), (1, 0, 0))
        self.assertIsInstance(record["score_before"], (int, float))
        self.assertIsInstance(record["score_after"], (int, float))
        self.assertEqual(record["facts_lost"], 0)

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

    def test_a_changed_file_is_not_comparable_even_at_the_same_file_count(self) -> None:
        """Same one file, same numbers, different bytes: the count cannot tell them apart."""
        self.measure("--save", str(self.baseline), str(self.corpus))
        path = self.corpus / "a.nix"
        path.write_text(SOURCE.replace("deep-thought", "hal-9000"), encoding="utf-8")

        code, out, _ = self.measure("--baseline", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 2)
        self.assertIn("a.nix", out)
        self.assertIn("changed since the baseline was recorded", out)

    def test_a_renamed_file_is_not_comparable(self) -> None:
        (self.corpus / "b.nix").write_text(SOURCE, encoding="utf-8")
        self.measure("--save", str(self.baseline), str(self.corpus))
        (self.corpus / "b.nix").rename(self.corpus / "c.nix")

        code, out, _ = self.measure("--baseline", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 2)
        self.assertIn("lost a file", out)
        self.assertIn("gained a file", out)

    def test_a_baseline_without_per_file_identity_is_reported_not_passed(self) -> None:
        self.measure("--save", str(self.baseline), str(self.corpus))
        recorded = json.loads(self.baseline.read_text(encoding="utf-8"))
        del recorded["corpus"]
        self.baseline.write_text(json.dumps(recorded), encoding="utf-8")

        code, out, _ = self.measure("--baseline", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 2)
        self.assertIn("no per-file identity", out)
        self.assertIn("--save", out)

    def test_a_fall_in_one_file_is_a_regression_even_when_the_corpus_score_is_flat(self) -> None:
        self.measure("--save", str(self.baseline), str(self.corpus))
        recorded = json.loads(self.baseline.read_text(encoding="utf-8"))
        recorded["corpus"][0]["score_after"] += 10
        self.baseline.write_text(json.dumps(recorded), encoding="utf-8")

        code, out, err = self.measure("--baseline", str(self.baseline), str(self.corpus))

        self.assertEqual(code, 1, err)
        self.assertIn("a.nix", out)
        self.assertIn("the score fell", out)

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
