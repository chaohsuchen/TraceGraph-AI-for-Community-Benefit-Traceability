from pathlib import Path
import unittest
from unittest.mock import Mock

from tracegraph.benchmark import _reference_cases, evaluate_workbook


WORKBOOK = Path(__file__).resolve().parents[1] / "Golden Dataset (5).xlsx"
H008 = "TG-GOLD-H008"
H009 = "TG-GOLD-H009"
H012 = "TG-GOLD-H012"


class BenchmarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference_cases = _reference_cases(WORKBOOK)

    def test_counts_three_of_ten_on_actual_authored_tabs(self):
        cases = [H008, H009, H012]
        returns = [
            {"result": {"findings": [{"supports": [self.reference_cases[cid][1][5]]}]}}
            for cid in cases
        ]
        researcher = Mock(side_effect=returns)

        report = evaluate_workbook(WORKBOOK, researcher, commitment_ids=cases)

        self.assertEqual(report["counts"]["reference"], 10)
        self.assertEqual(report["counts"]["matched"], 3)
        self.assertEqual(report["recall"], 0.3)
        self.assertEqual([case["counts"]["reference"] for case in report["cases"]], [3, 4, 3])
        self.assertEqual([case["recall"] for case in report["cases"]], [1 / 3, 1 / 4, 1 / 3])
        for cid, call in zip(cases, researcher.call_args_list, strict=True):
            self.assertEqual(call.args, (self.reference_cases[cid][0],))
            self.assertEqual(call.kwargs, {})
            self.assertNotIn(cid, call.args[0])
            for reference in self.reference_cases[cid][1].values():
                self.assertNotIn(reference["summary"], call.args[0])

    def test_duplicate_predictions_stay_reviewable_until_one_is_confirmed(self):
        reference = self.reference_cases[H008][1][5]
        returned = {"evidence": [reference, reference]}

        undecided = evaluate_workbook(WORKBOOK, lambda _: returned, commitment_ids=[H008])
        self.assertEqual(undecided["counts"]["matched"], 0)
        self.assertEqual(undecided["counts"]["review_required"], 2)

        report = evaluate_workbook(
            WORKBOOK, lambda _: returned, commitment_ids=[H008],
            reviewed_matches={H008: [{"reference_row": 5, "returned_index": 2}]},
        )
        self.assertEqual(report["counts"]["matched"], 1)
        self.assertEqual(report["counts"]["review_required"], 1)
        self.assertEqual(report["cases"][0]["matches"], [
            {"reference_row": 5, "returned_index": 2, "basis": "reviewed"},
        ])

    def test_keeps_novel_unknown_and_same_source_different_claim_separate(self):
        reference = self.reference_cases[H008][1][5]
        evidence = [
            {**reference, "summary": "The same page may describe a different claim."},
            {"source": {"url": "https://example.com/public-budget"}, "summary": "A new source."},
            {"summary": "An unsupported assertion."},
        ]

        report = evaluate_workbook(WORKBOOK, lambda _: {"evidence": evidence}, commitment_ids=[H008])

        case = report["cases"][0]
        self.assertEqual(case["counts"]["matched"], 0)
        self.assertEqual(case["counts"]["review_required"], 1)
        self.assertEqual(case["novel_candidate_indices"], [2])
        self.assertEqual(case["unknown_indices"], [3])
        self.assertEqual(case["missed_reference_rows"], [5, 6, 7])
        self.assertNotIn(reference["summary"], str(report))

    def test_default_is_two_and_missing_promise_has_no_evidence(self):
        researcher = Mock(return_value={"draft": None})
        report = evaluate_workbook(WORKBOOK, researcher)

        self.assertEqual(researcher.call_count, 2)
        self.assertEqual([case["commitment_id"] for case in report["cases"]], [H008, H009])
        self.assertEqual(report["counts"]["returned"], 0)
        self.assertEqual(report["recall"], 0.0)

    def test_invalid_selection_does_not_send_workbook_data(self):
        researcher = Mock()
        with self.assertRaises(ValueError):
            evaluate_workbook(WORKBOOK, researcher, commitment_ids=["TG-GOLD-A019"])
        researcher.assert_not_called()