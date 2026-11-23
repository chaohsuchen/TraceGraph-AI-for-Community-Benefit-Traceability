from importlib.util import find_spec
from pathlib import Path
from unittest import TestCase, skipUnless

from tracegraph.b0_rules import (
    RESORTS_WORLD,
    classify_obligation,
    extract_amount,
    extract_condition,
    extract_milestone,
    extract_periodicity,
    is_commitment_candidate,
    responsible_entity,
    unverifiable_reason,
    unverifiable_reason_v1_2,
)
from tracegraph.corpus import split_sentences
from tracegraph.extraction_eval import agree, is_empty, match


ROOT = Path(__file__).resolve().parents[1]
TIER1 = ROOT / "docs" / "tier1"
GOLD = ROOT / "evaluation" / "Golden Dataset (5).xlsx"


class ObligationRuleTests(TestCase):
    def test_classes_follow_taxonomy_cues(self):
        cases = {
            "Resorts World New York City shall pay a $500,000,000 license fee.": "Binding / license condition",
            "Resorts World also commits to up to $5 million per year for its program.": "Contractual commitment",
            "Construction workforce diversity goals are targeted for 5 percent women.": "Target / goal",
            "The operator will use best efforts to diversify leadership.": "Best effort",
            "The Applicant currently expects the facility to open in March 2026.": "Forecast / projection",
            "An annual commitment of the lesser of 2 percent of revenue or $25 million.": "Contingent commitment",
            "Resorts World has agreements in place with four unions covering 84 percent of the workforce.":
                "Reported accomplishment",
            "All Applicants are expected to track and report veteran employment.": "Unresolved / ambiguous",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(classify_obligation(text)[0], expected)

    def test_month_may_is_not_an_ambiguity_cue(self):
        self.assertEqual(classify_obligation("The licensee shall report by May 1, 2027.")[0],
                         "Binding / license condition")

    def test_undertaking_in_governing_instrument_is_binding(self):
        text = "commit to a community mitigation plan for the Host Municipality;"
        self.assertEqual(classify_obligation(text)[0], "Contractual commitment")
        self.assertEqual(classify_obligation(text, "RFA-2023-01-03")[0], "Binding / license condition")


class FieldPatternTests(TestCase):
    def test_amounts_keep_formulas_and_qualifiers(self):
        text = ("an annual commitment of the lesser of 2 percent of incremental gross gaming revenue "
                "above 2025 levels, or $25 million per year")
        self.assertTrue(extract_amount(text).startswith("lesser of 2 percent"))
        self.assertEqual(extract_amount("commits to up to $5 million per year (with $25 million upfront)"),
                         "up to $5 million per year; $25 million upfront")
        self.assertEqual(extract_amount("The licensee shall maintain records."), "unspecified")

    def test_term_milestone_and_condition(self):
        text = ("Upon award of a license, Resorts World New York City shall, on or before December 31, 2025, "
                "deposit an amount annually for the duration of the license term")
        self.assertIn("annual", extract_periodicity(text))
        self.assertIn("for the license term", extract_periodicity(text))
        self.assertIn("on or before December 31, 2025", extract_milestone(text))
        self.assertTrue(extract_condition(text).startswith("Upon award of a license"))

    def test_entity_prefers_named_obligor_then_section(self):
        self.assertEqual(responsible_entity("Resorts World shall pay.", "", "GFLB-SEL-2025-12-01"), RESORTS_WORLD)
        self.assertEqual(responsible_entity("The Applicant will build housing.", RESORTS_WORLD, "GFLB-SEL-2025-12-01"),
                         RESORTS_WORLD)
        self.assertEqual(responsible_entity("An Applicant must deposit funds.", "", "RFA-2023-01-03"), "Applicant")

    def test_candidate_filter(self):
        self.assertTrue(is_commitment_candidate("A Gaming Facility licensee must maintain a record of all agreements."))
        self.assertFalse(is_commitment_candidate("The Board will review each Application in turn today."))
        self.assertFalse(is_commitment_candidate("Shall pay."))


class VerifiabilityFilterTests(TestCase):
    def test_v1_1_drops_generic_regulator_and_reported_passages(self):
        self.assertEqual(unverifiable_reason("An Applicant must deposit funds.", "Applicant"), "generic obligor")
        self.assertEqual(unverifiable_reason("the Board expects stronger commitments.", RESORTS_WORLD),
                         "regulator voice")
        self.assertEqual(unverifiable_reason("expects the target to be 6 percent", RESORTS_WORLD,
                                             "The Board supports the 47 percent target, but "), "regulator voice")
        self.assertEqual(unverifiable_reason("The Applicant indicated that it assumed a 30-year term.", RESORTS_WORLD),
                         "reported speech")
        self.assertEqual(unverifiable_reason("Resorts World commits to $5 million per year.", RESORTS_WORLD), "")


    def test_v1_2_keeps_substantive_and_normative_passages(self):
        self.assertEqual(unverifiable_reason_v1_2("An Applicant must provide an instrument stating it will comply.",
                                                  "Applicant"), "generic administrative duty")
        self.assertEqual(unverifiable_reason_v1_2("The tax rate from slot machines shall be no less than 25 percent.",
                                                  "Applicant"), "")
        self.assertEqual(unverifiable_reason_v1_2("The Board was satisfied that it has agreements in place.",
                                                  RESORTS_WORLD), "regulator evaluation")
        self.assertEqual(unverifiable_reason_v1_2("expects the target to be 6 percent", RESORTS_WORLD,
                                                  "The Board supports the 47 percent target, but "), "regulator evaluation")
        self.assertEqual(unverifiable_reason_v1_2("The Board views the $25 million commitment favorably.", RESORTS_WORLD), "")
        self.assertEqual(unverifiable_reason_v1_2("the Board recommends that the license require the projects.",
                                                  RESORTS_WORLD), "")
        self.assertEqual(unverifiable_reason_v1_2("Resorts World having agreed to execute a monitoring agreement.",
                                                  RESORTS_WORLD), "recital")


class SegmentationTests(TestCase):
    def test_sentences_respect_abbreviations_and_clauses(self):
        text = ("Genting New York LLC d/b/a Resorts World will pay $5 million to the U.S. fund. "
                "The Board supports the permanent employment target of 47 percent women, but expects "
                "the veterans employment target to be the required 6 percent.")
        parts = [text[s:e] for s, e in split_sentences(text)]
        self.assertEqual(parts[0], "Genting New York LLC d/b/a Resorts World will pay $5 million to the U.S. fund.")
        self.assertTrue(parts[-1].startswith("expects the veterans"))


class ScoringTests(TestCase):
    def test_empty_markers(self):
        for value in ("", "unspecified", "none stated", "none named", "unspecified (not stated)"):
            self.assertTrue(is_empty(value), value)
        self.assertFalse(is_empty("none stated until 2027"))
        self.assertFalse(is_empty("Queens"))

    def test_agreement(self):
        self.assertTrue(agree("amount_value", "up to $5 million per year; $25 million upfront",
                              "up to $5 million per year; $25 million upfront"))
        self.assertFalse(agree("amount_value", "6 percent", "5 percent"))
        self.assertTrue(agree("responsible_entity", "Applicant",
                              "Applicant (generic; applies to Genting New York LLC d/b/a Resorts World)"))
        self.assertTrue(agree("obligation_type", "Target / goal", "target / goal"))

    def test_matching_is_one_to_one_and_needs_coverage(self):
        gold = [{"gold_id": "G1", "document_id": "D", "span": (0, 100)},
                {"gold_id": "G2", "document_id": "D", "span": (40, 60)}]
        preds = [{"pred_id": "P1", "document_id": "D", "char_start": 0, "char_end": 100},
                 {"pred_id": "P2", "document_id": "D", "char_start": 90, "char_end": 200}]
        self.assertEqual({g: p["pred_id"] for g, p in match(preds, gold).items()}, {"G1": "P1"})
        self.assertEqual(match(preds, gold, "strict").keys(), {"G1"})


@skipUnless(find_spec("pypdf") and find_spec("openpyxl") and TIER1.exists(), "Needs pypdf, openpyxl and Tier 1 PDFs.")
class CorpusIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        from tracegraph.b0_rules import extract_document
        from tracegraph.corpus import load_corpus
        from tracegraph.extraction_eval import align_gold, load_gold

        cls.documents = load_corpus(TIER1)
        cls.gold = align_gold(load_gold(GOLD), cls.documents)
        cls.predictions = [e.to_dict() for d in cls.documents for e in extract_document(d)]

    def test_every_gold_commitment_aligns_to_canonical_text(self):
        self.assertEqual([g["gold_id"] for g in self.gold if not g["span"]], [])

    def test_every_prediction_carries_exact_passage_provenance(self):
        texts = {d.document_id: d.text for d in self.documents}
        self.assertTrue(self.predictions)
        for p in self.predictions:
            self.assertEqual(texts[p["document_id"]][p["char_start"]:p["char_end"]], p["commitment_text"])
            self.assertTrue(p["passage_id"].startswith(f"{p['document_id']}:p{p['page']}:"))
            self.assertTrue(p["evidence_status"])

    def test_validated_only_gold_turns_unvalidated_hits_into_false_positives(self):
        from tracegraph.extraction_eval import evaluate, load_gold

        validated = load_gold(GOLD, validated_only=True)
        self.assertTrue(validated)
        self.assertTrue(all(g["human_validated"] for g in validated))
        self.assertLess(len(validated), len(self.gold))

        scored_docs = {g["document_id"] for g in self.gold}
        preds = [p for p in self.predictions if p["document_id"] in scored_docs]
        gold = [g for g in self.gold if g["human_validated"]]
        excluded = [g for g in self.gold if not g["human_validated"]]
        full = evaluate(preds, self.gold)["lenient"]["commitment"]["all"]
        report = evaluate(preds, gold, excluded=excluded)
        only = report["lenient"]["commitment"]["all"]
        moved = report["lenient"]["commitment"]["fp_on_excluded_gold"]
        self.assertGreater(moved, 0)
        # Every prediction is either a TP on validated gold or a FP; none is dropped.
        self.assertEqual(only["tp"] + only["fp"], len(preds))
        self.assertEqual(only["tp"] + only["fn"], len(gold))
        self.assertGreater(only["fp"], full["fp"])
        self.assertLessEqual(moved, only["fp"])

    def test_rule_versions_nest(self):
        from tracegraph.b0_rules import B0_V1_0, B0_V1_1, B0_V1_2, extract_document
        old = {e.passage_id for d in self.documents for e in extract_document(d, version=B0_V1_0)}
        for version in (B0_V1_1, B0_V1_2):
            new = [e.to_dict() for d in self.documents for e in extract_document(d, version=version)]
            self.assertTrue(new)
            self.assertTrue({p["passage_id"] for p in new} < old)
            self.assertTrue(all(p["extractor"] == version for p in new))

    def test_resplit_covers_every_gold_item(self):
        from tracegraph.extraction_eval import apply_split
        split = ROOT / "evaluation" / "splits" / "resplit_v2.json"
        gold = apply_split([dict(g) for g in self.gold], split)
        self.assertEqual({g["evaluation_split"] for g in gold}, {"dev", "test"})

    def test_extraction_is_deterministic(self):
        from tracegraph.b0_rules import extract_document
        again = [e.to_dict() for d in self.documents for e in extract_document(d)]
        self.assertEqual(again, self.predictions)
