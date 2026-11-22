import unittest
from unittest.mock import Mock, patch

from tests.test_agent import DISCOVERY, REFRESH, REFRESH_CLAIM, no_match, research_fixture
from tracegraph.agent import Agent
from tracegraph.reconcile import EvidenceReconciler
from tracegraph.runtime import create_application


def evidence(excerpt, *, url="https://example.com/implementation", locator="Section 1"):
    return {"source": {"title": "Implementation report", "url": url},
            "status": "Documented", "excerpt": excerpt, "locator": locator,
            "explanation": excerpt, "retrieved_at": "2026-11-22T12:00:00Z"}


def snapshot(*supports, revision=1):
    return {"revision": revision, "assessment": None, "relations": [],
            "findings": [{"id": f"old-{i}", "claim": item["excerpt"], "supports": [item]}
                         for i, item in enumerate(supports)]}


class CompanionRefreshTest(unittest.TestCase):
    def setUp(self):
        self.store, self.retrieval, self.companion = Mock(), Mock(), Mock()
        self.ordinary = evidence("A site operator was appointed.")
        self.additional = evidence("A payment was recorded in the register.")
        response = research_fixture()
        response["result"]["evidence"] = [self.ordinary]
        self.researcher = Mock(return_value=response)
        self.store.load_commitment.return_value = {"commitment": {"id": REFRESH["commitment_id"]}}
        self.store.research_snapshot.return_value = snapshot()
        self.store.finish_refresh.return_value = {"outcome": "COMMITTED"}
        self.assessor = Mock(return_value={"label": "UNVERIFIED", "verdict": "Assessment of combined evidence.",
                                           "reasoning": "Timing remains open."})
        self.agent = Agent(self.retrieval, self.store, self.researcher,
                           EvidenceReconciler(self.assign, assessor=self.assessor),
                           companion_research=self.companion)

    @staticmethod
    def assign(context, state, observations, reviewed):
        return {"assignments": [{"observation_id": row["id"], "finding_id": "new:" + row["id"],
                                 "claim": row["support"]["excerpt"]} for row in observations], "relations": []}

    def test_refresh_uses_one_snapshot_for_deduplicated_graph_and_assessment(self):
        saved = evidence("The original pledge set a deadline.")
        self.store.research_snapshot.return_value = snapshot(saved)
        self.companion.return_value = [
            {**saved, "source": {**saved["source"], "url": saved["source"]["url"] + "#page=3"},
             "locator": "Characters 10–43"}, self.ordinary, self.additional,
            {**self.additional, "locator": "Page 3", "excerpt": self.additional["excerpt"] + "  "},
            {**self.additional, "excerpt": None, "summary": "Text without a verified quote"},
            {**self.additional, "retrieved_at": "invalid"}, None,
        ]
        self.agent.execute(REFRESH)
        self.companion.assert_called_once_with(self.store.load_commitment.return_value,
                                               self.store.research_snapshot.return_value,
                                               self.researcher.return_value["result"])
        assessed = self.assessor.call_args.args[1]["findings"]
        claim, result = self.store.finish_refresh.call_args.args
        self.assertEqual(claim, REFRESH_CLAIM)
        self.assertEqual(result["findings"], assessed)
        self.assertEqual([f["supports"] for f in assessed], [[saved], [self.ordinary], [self.additional]])
        self.assertEqual(result["verdict"], self.assessor.return_value["verdict"])
        self.store.fail.assert_not_called()

    def test_revision_retry_reuses_passages_and_sees_concurrent_evidence(self):
        self.store.research_snapshot.side_effect = [snapshot(), snapshot(self.additional, revision=2)]
        self.store.finish_refresh.side_effect = [{"outcome": "RECHECK_REQUIRED"}, {"outcome": "COMMITTED"}]
        self.companion.return_value = [self.additional]
        self.agent.execute(REFRESH)
        self.companion.assert_called_once()
        self.researcher.assert_called_once()
        first, second = [call.args[1] for call in self.store.finish_refresh.call_args_list]
        self.assertEqual(first["evidence"], [self.ordinary, self.additional])
        self.assertEqual(second["evidence"], [self.ordinary])
        self.assertEqual(second["expected_revision"], 2)
        self.assertEqual([f["supports"] for f in second["findings"]], [[self.additional], [self.ordinary]])

    def test_optional_error_preserves_ordinary_result(self):
        self.companion.side_effect = TimeoutError("Optional search unavailable")
        with self.assertLogs("tracegraph.agent", level="WARNING"):
            self.agent.execute(REFRESH)
        self.assertEqual(self.store.finish_refresh.call_args.args[1]["evidence"], [self.ordinary])
        self.store.fail.assert_not_called()

    def test_reconciliation_error_with_additional_passages_falls_back_without_searching_again(self):
        original = evidence("No site operator was appointed.")
        review = evidence("The abandoned follow-up mentioned an appointment.")
        self.store.research_snapshot.return_value = snapshot(original)
        self.companion.return_value = [self.additional]
        self.assessor.side_effect = [RuntimeError("Combined assessment too large"), self.assessor.return_value]
        followup = Mock(return_value=[review])

        def propose_conflict(context, state, observations, reviewed):
            plan = self.assign(context, state, observations, reviewed)
            if not reviewed:
                plan["relations"] = [{"kind": "CONFLICT", "from_id": "old-0", "to_id": "new:o1",
                                      "status": "UNRESOLVED", "reasoning": "Records disagree about appointment."}]
            return plan

        reconciler = EvidenceReconciler(propose_conflict, assessor=self.assessor, followup=followup)
        agent = Agent(self.retrieval, self.store, self.researcher, reconciler, companion_research=self.companion)
        with self.assertLogs("tracegraph.agent", level="WARNING"):
            agent.execute(REFRESH)
        result = self.store.finish_refresh.call_args.args[1]
        self.assertEqual([f["supports"] for f in result["findings"]], [[original], [self.ordinary]])
        self.assertEqual(result["relations"][0]["status"], "UNRESOLVED")
        self.assertEqual(self.assessor.call_count, 2)
        followup.assert_called_once()
        self.companion.assert_called_once()
        self.researcher.assert_called_once()
        self.store.fail.assert_not_called()

    def test_lookup_and_first_discovery_do_not_run_companion_research(self):
        self.retrieval.resolve.return_value = no_match("revision")
        self.retrieval.embed_commitment.return_value = [0.5]
        self.store.finish_discovery.return_value = {"outcome": "COMMITTED"}
        self.assertEqual(self.agent.lookup(DISCOVERY["query"])["outcome"], "START_DISCOVERY")
        self.agent.execute(DISCOVERY)
        self.store.finish_discovery.assert_called_once()
        self.store.research_snapshot.assert_not_called()
        self.companion.assert_not_called()

    def test_runtime_enables_companions_only_for_live_web_and_document_access(self):
        with (patch("tracegraph.runtime.check_embedding_profile"),
              patch("tracegraph.runtime.read_credential", return_value="fixture credential"),
              patch("tracegraph.companion_documents.CompanionDocumentResearcher") as companion):
            for provider in ("openai", "anthropic"):
                live = create_application("stub", "live", provider=provider)
                companion.assert_called_once_with(live.agent._researcher)
                self.assertIs(live.agent._companion_research, companion.return_value)
                companion.reset_mock()
            for mode, options in (("fixture", {}), ("live", {"allow_source_recovery": False}),
                                  ("live", {"allow_web_search": False, "allow_source_recovery": True})):
                with self.subTest(mode=mode, options=options):
                    offline = create_application("stub", mode, **options)
                    self.assertIsNone(offline.agent._companion_research)
                    companion.assert_not_called()
