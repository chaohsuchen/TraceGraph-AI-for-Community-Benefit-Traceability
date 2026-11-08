import unittest
from unittest.mock import Mock

from tests.test_agent import (
    DISCOVERY, DISCOVERY_CLAIM, REFRESH, REFRESH_CLAIM, no_match, research_fixture,
)
from tracegraph.agent import Agent


EMPTY_SNAPSHOT = {"revision": 0, "findings": [], "relations": [], "assessment": None}


class AgentV2Test(unittest.TestCase):
    def setUp(self):
        self.retrieval = Mock()
        self.store = Mock()
        self.researcher = Mock(return_value=research_fixture())
        self.reconciler = Mock()
        self.reconciler.reconcile.return_value = {
            "label": "DOCUMENTED",
            "verdict": "The promise is documented.",
            "reasoning": "The original source records the promise.",
            "findings": [{"id": "finding-1", "claim": {}, "supports": []}],
            "relations": [],
        }
        self.context = {"commitment": {"id": REFRESH["commitment_id"]}, "organization": None}
        self.store.load_commitment.return_value = self.context
        self.agent = Agent(self.retrieval, self.store, self.researcher, self.reconciler)

    def test_discovery_reconciles_once_against_empty_graph(self):
        self.retrieval.resolve.side_effect = [no_match("rev-1"), no_match("rev-2")]
        self.retrieval.embed_commitment.return_value = [0.25, 0.75]
        self.store.finish_discovery.side_effect = [
            {"outcome": "RECHECK_REQUIRED"},
            {"outcome": "COMMITTED", "commitment_id": "new", "run_id": None},
        ]

        self.agent.execute(DISCOVERY)

        self.reconciler.reconcile.assert_called_once()
        self.store.research_snapshot.assert_not_called()
        call = self.reconciler.reconcile.call_args
        context, snapshot, original_result = call.args
        self.assertEqual(context["commitment"]["title"], self.researcher.return_value["draft"]["title"])
        self.assertNotIn("embedding", context["commitment"])
        self.assertEqual(snapshot, EMPTY_SNAPSHOT)
        self.assertEqual(original_result, self.researcher.return_value["result"])
        self.assertEqual(call.kwargs["followup_cache"], {})
        claim, revision, _, result = self.store.finish_discovery.call_args.args
        self.assertEqual((claim, revision, result["expected_revision"]), (DISCOVERY_CLAIM, "rev-2", 0))
        self.assertEqual(result["label"], "DOCUMENTED")
        self.assertEqual(result["findings"], self.reconciler.reconcile.return_value["findings"])
        self.assertEqual(result["relations"], [])
        self.store.fail.assert_not_called()

    def test_refresh_reloads_snapshot_without_repeating_search_or_followup(self):
        old = {"revision": 6, "findings": [], "relations": [], "assessment": None}
        current = {**old, "revision": 7, "findings": [{"id": "parallel-write"}]}
        self.store.research_snapshot.side_effect = [old, current]
        self.store.finish_refresh.side_effect = [
            {"outcome": "RECHECK_REQUIRED"},
            {"outcome": "COMMITTED", "commitment_id": REFRESH["commitment_id"], "run_id": None},
        ]

        def reconcile(context, snapshot, result, *, followup_cache):
            if snapshot["revision"] == 6:
                followup_cache["followup"] = "reused evidence"
            else:
                self.assertEqual(followup_cache.get("followup"), "reused evidence")
            return self.reconciler.reconcile.return_value

        self.reconciler.reconcile.side_effect = reconcile

        self.agent.execute(REFRESH)

        self.researcher.assert_called_once_with(REFRESH, self.context)
        first, second = self.reconciler.reconcile.call_args_list
        self.assertEqual([first.args[1], second.args[1]], [old, current])
        self.assertIs(first.args[2], second.args[2])
        self.assertIs(first.kwargs["followup_cache"], second.kwargs["followup_cache"])
        writes = [call.args for call in self.store.finish_refresh.call_args_list]
        self.assertEqual([claim for claim, _ in writes], [REFRESH_CLAIM, REFRESH_CLAIM])
        self.assertEqual([result["expected_revision"] for _, result in writes], [6, 7])
        self.store.fail.assert_not_called()

    def test_refresh_retry_limit_records_retryable_failure(self):
        self.store.research_snapshot.return_value = EMPTY_SNAPSHOT
        self.store.finish_refresh.return_value = {"outcome": "RECHECK_REQUIRED"}

        self.agent.execute(REFRESH)

        self.researcher.assert_called_once()
        self.assertEqual(self.store.finish_refresh.call_count, 3)
        claim, error = self.store.fail.call_args.args
        self.assertEqual(claim, REFRESH_CLAIM)
        self.assertEqual(error["code"], "RESEARCH_UNAVAILABLE")
        self.assertTrue(error["retryable"])

    def test_reconciliation_failure_writes_no_assessment(self):
        self.store.research_snapshot.return_value = EMPTY_SNAPSHOT
        self.reconciler.reconcile.side_effect = RuntimeError("Malformed provider plan")

        self.agent.execute(REFRESH)

        self.store.finish_refresh.assert_not_called()
        claim, error = self.store.fail.call_args.args
        self.assertEqual(claim, REFRESH_CLAIM)
        self.assertEqual(error["code"], "RESEARCH_UNAVAILABLE")