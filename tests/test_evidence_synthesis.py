from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from tracegraph.discovery import discover
from tracegraph.evidence_synthesis import observations, synthesize
from tracegraph.providers import ProviderError


STAMP = "2026-01-01T00:00:00+00:00"
CITATIONS = {"c1": {"url": "https://example.org/a", "title": "Original",
                    "excerpt": "Alder announced a fund.  The fund would award grants."}}


def selection(cid, excerpt, gap=""):
    return {"findings": [{"citation_id": cid, "summary": excerpt, "status": "Promise documented",
                          "explanation": "The source documents the terms.", "excerpt": excerpt, "locator": ""}],
            "gap": gap, "followup_query": "Alder first awards report" if gap else ""}


class SynthesisTests(TestCase):
    def test_selects_owned_contiguous_sentences_and_groups_clauses_from_same_span(self):
        atom = {"citation_id": "c1", "first": 1, "last": 2, "summary": "Alder announced a fund.",
                "status": "Promise documented", "explanation": "Alder announced it."}
        reply = {"findings": [atom, {**atom, "summary": "The fund would award grants."}],
                 "followup_query": "", "gap": ""}
        with patch("tracegraph.evidence_synthesis._structured", return_value=reply):
            selected = synthesize(None, "What was promised?", CITATIONS)
        evidence = observations(selected, CITATIONS, STAMP)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["excerpt"], CITATIONS["c1"]["excerpt"])
        self.assertIn("would award grants", evidence[0]["summary"])
        self.assertEqual(evidence[0]["source"]["url"], CITATIONS["c1"]["url"])

    def test_invalid_range_cannot_be_attached_or_redirected(self):
        atom = {"citation_id": "c1", "first": 1, "last": 3,
                "summary": "Wrong", "status": "Context", "explanation": "Unrelated"}
        with patch("tracegraph.evidence_synthesis._structured", return_value={
            "findings": [atom], "followup_query": "", "gap": ""}):
            with self.assertRaises(ProviderError):
                synthesize(None, "query", CITATIONS)
        with self.assertRaises(ProviderError):
            observations(selection("c1", "A different passage."), CITATIONS, STAMP)

    def test_empty_capture_has_no_model_or_web_request(self):
        with patch("tracegraph.evidence_synthesis._structured") as submit:
            self.assertEqual(synthesize(None, "query", {})["findings"], [])
            submit.assert_not_called()

    def flow(self, replies, *, searched=None, enabled=True, base=None):
        events = []
        researcher = SimpleNamespace(
            _client=None, evidence_synthesizer=Mock(side_effect=replies), evidence_followup=enabled,
            _allow_source_recovery=True, _remaining_search_uses=2, _emit=events.append,
            _search=Mock(side_effect=[searched] if searched is not None else AssertionError("unexpected search")))
        initial = base if base is not None else {"draft": {"title": "Alder fund"}, "result": {"evidence": []}}
        with patch("tracegraph.discovery._discover", return_value=deepcopy(initial)):
            result = discover(researcher, {"target": "Alder's grant fund"}, CITATIONS, STAMP)
        return result, researcher, events

    def test_concrete_gap_uses_once_and_preserves_first_evidence(self):
        old = selection("c1", "Alder announced a fund.", "Who received the first grant?")
        new = selection("c2", "Birch received the first grant.")
        found = {"web1": {"title": "Grant report", "url": "https://example.org/b",
                          "excerpt": "Birch received the first grant."}}
        result, researcher, events = self.flow([old, new], searched=("report", found))
        self.assertEqual({item["excerpt"] for item in result["result"]["evidence"]},
                         {old["findings"][0]["excerpt"], new["findings"][0]["excerpt"]})
        researcher._search.assert_called_once()
        self.assertEqual([e["new_verified_excerpts"] for e in events if e["type"] == "evidence_gap_search"], [1])

    def test_no_gap_or_disabled_followup_never_calls_search(self):
        for gap, enabled in [("", True), ("Any grant recipients?", False)]:
            with self.subTest(gap=gap, enabled=enabled):
                item = selection("c1", "Alder announced a fund.", gap)
                _, researcher, events = self.flow([item], enabled=enabled)
                researcher._search.assert_not_called()
                decision = next(e for e in events if e["type"] == "evidence_gap_search")
                self.assertFalse(decision["performed"])
                self.assertEqual(decision["recommended"], bool(gap))

    def test_failed_optional_enrichment_preserves_observations(self):
        original = selection("c1", "Alder announced a fund.", "Who received an award?")
        result, _, events = self.flow([original], searched=ProviderError("unavailable"))
        self.assertEqual(result["result"]["evidence"][0]["excerpt"], original["findings"][0]["excerpt"])
        self.assertTrue(any(e.get("error") == "ProviderError" for e in events))

    def test_partial_preserves_prior_data_on_empty_or_rejected_new_review(self):
        partial = {"draft": None, "target_status": "IDENTIFIED", "gaps": ["Scope unknown."],
                   "related_evidence": [{"excerpt": "Prior verified fact."}]}
        for response in ({"findings": [], "gap": "", "followup_query": ""}, ProviderError("invalid selection")):
            result, researcher, _ = self.flow([response], base=partial)
            self.assertEqual(result["related_evidence"], partial["related_evidence"])
            self.assertIsNone(result["draft"])
            researcher._search.assert_not_called()

    def test_ambiguous_candidates_do_not_receive_a_synthesized_shared_answer(self):
        base = {"draft": None, "target_status": "AMBIGUOUS", "clarification_question": "Which site?"}
        result, researcher, _ = self.flow([], base=base)
        self.assertEqual(result, base)
        researcher.evidence_synthesizer.assert_not_called()
