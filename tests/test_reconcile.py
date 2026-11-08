import json
import unittest
from unittest.mock import Mock

from tracegraph.providers import ClaudeReconciliationJudge, ClaudeWebResearcher
from tracegraph.reconcile import EvidenceReconciler, ReconciliationError
from tests.test_providers import _json_message, _search_message, _structured_finding


_CONTEXT = {
    "commitment": {"description": "The authority promised five overnight shuttle routes.",
                   "notes": "private workbook note"},
    "organization": {"id": "org-private-id", "name": "Transit Authority"},
}
_VERIFICATION_DATE = "2026-11-07"
_ASSESSMENT = {
    "label": "PARTIALLY_CORROBORATED", "verdict": "Some routes are running.",
    "reasoning": "The published route list records three of the promised five routes.",
}


def _support(page, excerpt):
    return {
        "source": {"url": f"https://transit.example/{page}", "title": page},
        "excerpt": excerpt, "summary": None, "locator": "Section 1",
        "status": "Published record", "explanation": "Direct reporting.",
        "retrieved_at": "2026-11-07T12:00:00+00:00",
    }


def _snapshot(*findings, relations=(), revision=4):
    return {"revision": revision, "findings": list(findings), "relations": list(relations), "assessment": None}


def _research(*evidence):
    return {"verification_date": _VERIFICATION_DATE, "evidence": list(evidence)}


def _assignment(observation_id, finding_id, claim=""):
    return {"observation_id": observation_id, "finding_id": finding_id, "claim": claim}


def _link(kind, from_id, to_id, *, status=None, preferred_id=None, support_ids=()):
    return {"kind": kind, "from_id": from_id, "to_id": to_id, "status": status,
            "preferred_id": preferred_id, "support_ids": list(support_ids), "reasoning": "Same scope and period."}


def _plan(assignments=(), relations=(), assessment=None):
    return {"assignments": list(assignments), "relations": list(relations),
            "assessment": assessment or dict(_ASSESSMENT)}


class ReconciliationTests(unittest.TestCase):
    def test_repeated_excerpt_is_deduplicated_and_paraphrases_keep_existing_id(self):
        support = _support("board-report", "Three routes began operating.")
        prior = _snapshot({"id": "f-1", "claim": "Three routes opened", "supports": [support]})
        newer = _support("city-audit", "The Authority began service on 3 routes.")

        def judge(context, snapshot, observations, reviewed_pairs):
            self.assertEqual([item["support"] for item in observations], [newer])
            return _plan([_assignment("o1", "f-1")])

        def assess(context, state):
            self.assertEqual(len(state["findings"]), 1)
            self.assertEqual(state["findings"][0]["supports"], [support, newer])
            return dict(_ASSESSMENT)

        duplicate = dict(support, retrieved_at="2026-11-07T14:00:00+00:00")
        result = EvidenceReconciler(judge, assessor=assess).reconcile(
            _CONTEXT, prior, _research(duplicate, newer, newer)
        )
        self.assertEqual(result["expected_revision"], 4)
        self.assertEqual(result["verification_date"], _VERIFICATION_DATE)
        self.assertEqual(result["findings"][0]["id"], "f-1")
        self.assertEqual(result["label"], "PARTIALLY_CORROBORATED")
        self.assertEqual(prior["findings"][0]["supports"], [support])

    def test_exact_claim_reuses_canonical_finding_even_if_model_creates_alias(self):
        prior = _snapshot({"id": "f-1", "claim": "Three routes opened", "supports": [
            _support("board-report", "Three routes began operating.")
        ]})
        judge = Mock(return_value=_plan([_assignment("o1", "new:1", "THREE routes   opened")]))
        result = EvidenceReconciler(judge).reconcile(
            _CONTEXT, prior, _research(_support("city-audit", "Three routes operate."))
        )
        self.assertEqual([finding["id"] for finding in result["findings"]], ["f-1"])
        self.assertEqual(len(result["findings"][0]["supports"]), 2)

    def test_progress_points_from_prior_to_new_milestone(self):
        prior = _snapshot({"id": "f-1", "claim": "The Board approved a shuttle pilot.", "supports": [
            _support("approval", "The Board approved the pilot on June 4.")
        ]})
        judge = Mock(return_value=_plan(
            [_assignment("o1", "new:1", "Three routes entered service in October.")],
            [_link("PROGRESS", "f-1", "new:1")],
        ))
        result = EvidenceReconciler(judge).reconcile(
            _CONTEXT, prior, _research(_support("launch", "Three routes entered service in October."))
        )
        later = result["findings"][1]["id"]
        self.assertEqual(result["relations"], [{
            "kind": "PROGRESS", "from_id": "f-1", "to_id": later, "status": None,
            "preferred_id": None, "reasoning": "Same scope and period.",
        }])

    def test_rejects_cycles_and_invented_ids(self):
        prior = _snapshot(
            {"id": "f-1", "claim": "One route entered service.", "supports": [_support("one", "First route.")]},
            {"id": "f-2", "claim": "Three routes entered service.", "supports": [_support("three", "Three routes.")]},
            relations=[_link("PROGRESS", "f-1", "f-2")],
        )
        judge = Mock()
        reconciler = EvidenceReconciler(judge)
        for edge, failure in [
            (_link("PROGRESS", "f-2", "f-1"), "form a cycle"),
            (_link("PROGRESS", "f-2", "invented"), "unknown finding"),
        ]:
            judge.return_value = _plan(relations=[edge])
            with self.assertRaisesRegex(ReconciliationError, failure):
                reconciler.reconcile(_CONTEXT, prior, _research())

    def test_both_conflict_sides_survive_and_review_is_reused_on_retry(self):
        prior = _snapshot({"id": "f-1", "claim": "Three routes were active on November 1.", "supports": [
            _support("authority", "We operated three routes November 1.")
        ]})
        contradiction = _support("notice", "All routes had suspended service on November 1.")
        corroboration = _support("audit", "Vehicle logs show three routes in service November 1.")

        def judge(context, snapshot, observations, pairs):
            self.assertEqual(len(observations), 1)
            if not pairs:
                return _plan(
                    [_assignment("o1", "new:1", "No routes were active on November 1.")],
                    [_link("CONFLICT", "f-1", "new:1", status="UNRESOLVED")],
                )
            self.assertTrue(observations[0]["from_review"])
            return _plan(
                [_assignment("o1", "f-1")],
                [_link("CONFLICT", pairs[0]["to_id"], "f-1", status="RESOLVED",
                       preferred_id="f-1", support_ids=["o1"])],
            )

        followup = Mock(return_value=[corroboration])
        assessor = Mock(return_value=_ASSESSMENT)
        reconciler = EvidenceReconciler(judge, followup=followup, assessor=assessor)
        cache = {}
        first = reconciler.reconcile(_CONTEXT, prior, _research(contradiction), followup_cache=cache)
        second = reconciler.reconcile(
            _CONTEXT, {**prior, "revision": 5}, _research(contradiction), followup_cache=cache
        )
        self.assertEqual(second["expected_revision"], 5)
        self.assertEqual(first["findings"], second["findings"])
        self.assertEqual([len(finding["supports"]) for finding in first["findings"]], [2, 1])
        edge = first["relations"][0]
        self.assertEqual(edge["status"], "RESOLVED")
        self.assertEqual(edge["preferred_id"], "f-1")
        self.assertIn("https://transit.example/audit", edge["reasoning"])
        followup.assert_called_once()
        self.assertEqual(assessor.call_args.args[1]["relations"], second["relations"])

    def test_no_new_review_evidence_keeps_conflict_unresolved(self):
        support = _support("notice", "All routes had suspended service on November 1.")
        judge = Mock(return_value=_plan(
            [_assignment("o1", "new:1", "Three routes were active on November 1."),
             _assignment("o2", "new:2", "No routes were active on November 1.")],
            [_link("CONFLICT", "new:1", "new:2", status="UNRESOLVED")],
        ))
        followup = Mock(return_value=[support])
        result = EvidenceReconciler(judge, followup=followup).reconcile(
            _CONTEXT, _snapshot(), _research(_support("operator", "Three were running November 1."), support)
        )
        self.assertEqual(result["label"], "CONFLICTING")
        self.assertEqual(result["relations"][0]["status"], "UNRESOLVED")
        self.assertIsNone(result["relations"][0]["preferred_id"])
        judge.assert_called_once()

    def test_cannot_resolve_conflict_without_targeted_evidence_for_preferred_side(self):
        first = _support("operator", "Three were running November 1.")
        second = _support("notice", "No routes were running November 1.")
        assignments = [_assignment("o1", "new:1", "Three operating"), _assignment("o2", "new:2", "None operating")]
        judge = Mock(return_value=_plan(assignments, [
            _link("CONFLICT", "new:1", "new:2", status="RESOLVED", preferred_id="new:1", support_ids=["o1"]),
        ]))
        with self.assertRaisesRegex(ReconciliationError, "after targeted review"):
            EvidenceReconciler(judge).reconcile(_CONTEXT, _snapshot(), _research(first, second))

        def unresolved_then_forged(context, snapshot, observations, pairs):
            if not pairs:
                return _plan(assignments, [_link("CONFLICT", "new:1", "new:2", status="UNRESOLVED")])
            left, right = pairs[0]["from_id"], pairs[0]["to_id"]
            return _plan([_assignment("o1", right)], [
                _link("CONFLICT", left, right, status="RESOLVED", preferred_id=left, support_ids=["o1"]),
            ])

        followup = Mock(return_value=[_support("audit", "Independent records support none operating.")])
        with self.assertRaisesRegex(ReconciliationError, "must support the preferred finding"):
            EvidenceReconciler(unresolved_then_forged, followup=followup).reconcile(
                _CONTEXT, _snapshot(), _research(first, second)
            )

    def test_rejects_omitted_and_invented_observations(self):
        judge = Mock()
        reconciler = EvidenceReconciler(judge)
        result = _research(_support("operator", "Three were running November 1."))
        judge.return_value = _plan()
        with self.assertRaisesRegex(ReconciliationError, "Every new observation"):
            reconciler.reconcile(_CONTEXT, _snapshot(), result)
        judge.return_value = _plan([_assignment("o2", "new:1", "Forged claim")])
        with self.assertRaisesRegex(ReconciliationError, "unknown or assigned more than once"):
            reconciler.reconcile(_CONTEXT, _snapshot(), result)


class ClaudeReconciliationTests(unittest.TestCase):
    def test_assessor_receives_only_the_normalized_evidence_and_public_context(self):
        client = Mock()
        client.create.side_effect = [
            _json_message(_plan([_assignment("o1", "new:1", "Three routes opened.")])),
            _json_message(_ASSESSMENT),
        ]
        judge = ClaudeReconciliationJudge(client)
        evidence = _support("board", "Three routes opened.")
        evidence["source"]["annotator"] = "private annotator"
        result = EvidenceReconciler(judge, assessor=judge.assess).reconcile(
            _CONTEXT, _snapshot(), _research(evidence)
        )
        self.assertEqual(result["label"], _ASSESSMENT["label"])
        planner, assessor = [json.loads(call.kwargs["messages"][0]["content"])
                             for call in client.create.call_args_list]
        self.assertEqual(len(planner["observations"]), 1)
        self.assertEqual(assessor["findings"][0]["claim"], "Three routes opened.")
        self.assertEqual(len(assessor["findings"][0]["supports"]), 1)
        for body in (planner, assessor):
            serialized = json.dumps(body)
            self.assertNotIn("private workbook note", serialized)
            self.assertNotIn("private annotator", serialized)
            self.assertNotIn("org-private-id", serialized)
        self.assertNotIn("observations", assessor)

    def test_conflict_review_uses_targeted_search_and_verified_citations(self):
        client = Mock()
        client.create.side_effect = [
            _search_message(), _json_message({"findings": [_structured_finding("c2")]})
        ]
        pair = {"from_id": "internal-finding", "from_claim": "Five routes opened.",
                "to_id": "internal-other-finding", "to_claim": "Three routes opened."}
        evidence = ClaudeWebResearcher(client).review_conflicts(_CONTEXT, [pair])
        self.assertEqual(evidence[0]["source"]["url"], "https://transit.example/public/service-bulletin")
        search = client.create.call_args_list[0].kwargs
        self.assertEqual(search["tools"][0]["max_uses"], 3)
        body = json.loads(search["messages"][0]["content"])
        self.assertEqual(body["conflicts"], [{"from_claim": "Five routes opened.", "to_claim": "Three routes opened."}])
        self.assertNotIn("private workbook note", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
