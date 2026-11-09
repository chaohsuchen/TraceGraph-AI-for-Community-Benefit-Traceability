import unittest
from unittest.mock import Mock

from tracegraph.agent import Agent, ResolutionUnavailable


DISCOVERY = {
    "kind": "DISCOVER",
    "job_id": "fixture-job-discovery",
    "claim_token": "fixture-token-discovery",
    "query": "What did Bally's pledge for the police substation and annual support?",
}
REFRESH = {
    "kind": "REFRESH",
    "job_id": "fixture-job-refresh",
    "claim_token": "fixture-token-refresh",
    "commitment_id": "TG-GOLD-H008",
}
DISCOVERY_CLAIM = {field: DISCOVERY[field] for field in ("job_id", "claim_token")}
REFRESH_CLAIM = {field: REFRESH[field] for field in ("job_id", "claim_token")}


def research_fixture():
    source = {
        "title": "Simulated public safety commitment",
        "url": "https://example.com/public-safety",
        "citation": None,
        "publisher": "Fixture publisher",
        "source_type": "public statement",
        "published_on": "2025-12-01",
    }
    return {
        "draft": {
            "title": "Bally's public safety support",
            "description": "Up to $10 million for a new substation and $5 million annually for public safety.",
            "original_text": "Up to $10 million for a new substation and $5 million annually for public safety.",
            "organization_id": "fixture-org-ballys",
            "organization_name": "Bally's Corporation",
            "amount_value": "Up to $10 million capital and $5 million annual",
            "term_periodicity": "One-time capital and annual support",
            "original_promise": {
                "source": source,
                "locator": "Public safety, page 13",
                "excerpt": "Up to $10 million for a new substation and $5 million annually for public safety.",
            },
        },
        "result": {
            "verification_date": "2026-11-07",
            "verdict": "Commitment confirmed. Delivery unverified.",
            "reasoning": "The fixture documents the promise. The source does not record payments.",
            "evidence": [{
                "source": source,
                "status": "Commitment documented",
                "explanation": "The statement sets out the two funding amounts.",
                "excerpt": None,
                "summary": "The fixture describes capital and annual support.",
                "locator": "Public safety, page 13",
                "retrieved_at": "2026-11-06T15:00:00Z",
            }],
        },
    }


def no_match(revision):
    return {"outcome": "NO_MATCH", "catalog_revision": revision}


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.retrieval = Mock()
        self.store = Mock()
        self.researcher = Mock(return_value=research_fixture())
        self.agent = Agent(self.retrieval, self.store, self.researcher)

    def test_lookup_routes_all_identity_outcomes(self):
        cases = [
            ({"outcome": "MATCH", "commitment_id": "TG-GOLD-H008"},
             {"outcome": "MATCH", "commitment_id": "TG-GOLD-H008"}),
            ({"outcome": "AMBIGUOUS", "question": "Construction or operations?"},
             {"outcome": "AMBIGUOUS", "question": "Construction or operations?"}),
            (no_match("rev-1"), {"outcome": "START_DISCOVERY", "query": DISCOVERY["query"]}),
        ]
        for decision, expected in cases:
            with self.subTest(outcome=decision["outcome"]):
                self.retrieval.resolve.return_value = decision
                self.assertEqual(self.agent.lookup(" " + DISCOVERY["query"] + " "), expected)
                self.retrieval.resolve.assert_called_with({"query": DISCOVERY["query"]})
        self.researcher.assert_not_called()
        self.assertEqual(self.store.mock_calls, [])

    def test_lookup_invalid_model_response_is_unavailable(self):
        self.retrieval.resolve.return_value = {"outcome": "MATCH"}
        with self.assertRaises(ResolutionUnavailable):
            self.agent.lookup(DISCOVERY["query"])
        self.researcher.assert_not_called()
        self.assertEqual(self.store.mock_calls, [])

    def test_blank_lookup_query_does_not_call_retrieval(self):
        with self.assertRaises(ValueError):
            self.agent.lookup("   ")
        self.retrieval.resolve.assert_not_called()

    def test_discovery_requires_locatable_original_promise(self):
        for missing_field in (None, "source", "locator", "excerpt"):
            with self.subTest(missing_field=missing_field):
                fixture = research_fixture()
                if missing_field is None:
                    fixture["draft"] = None
                else:
                    del fixture["draft"]["original_promise"][missing_field]
                self.researcher.return_value = fixture
                self.agent.execute(DISCOVERY)
                claim, result = self.store.finish_without_run.call_args.args
                self.assertEqual(claim, DISCOVERY_CLAIM)
                self.assertEqual(result["outcome"], "NO_PUBLIC_PROMISE")
        self.retrieval.resolve.assert_not_called()
        self.store.finish_discovery.assert_not_called()
        self.store.fail.assert_not_called()

    def test_discovery_reports_missing_scope_without_saving_a_narrower_record(self):
        missing = ["ferry shuttle expansion", "new bus stops"]
        for draft in (None, research_fixture()["draft"]):
            with self.subTest(has_draft=draft is not None):
                self.researcher.return_value = {"draft": draft, "missing_scope": missing}
                self.agent.execute(DISCOVERY)
                claim, result = self.store.finish_without_run.call_args.args
                self.assertEqual(claim, DISCOVERY_CLAIM)
                self.assertEqual(result["outcome"], "NO_PUBLIC_PROMISE")
                for component in missing:
                    self.assertIn(component, result["message"])
        self.retrieval.resolve.assert_not_called()
        self.store.finish_discovery.assert_not_called()
        self.store.fail.assert_not_called()

    def test_discovery_passes_requested_scope_to_reconciliation_only(self):
        criteria = ["Funding for the police substation", "Annual public safety support"]
        self.researcher.return_value["criteria"] = criteria
        self.retrieval.resolve.return_value = no_match("rev-1")
        self.retrieval.embed_commitment.return_value = [0.25, 0.75]
        self.store.finish_discovery.return_value = {"outcome": "COMMITTED"}
        reconciler = Mock()
        reconciler.reconcile.return_value = {}

        Agent(self.retrieval, self.store, self.researcher, reconciler).execute(DISCOVERY)

        context = reconciler.reconcile.call_args.args[0]
        self.assertEqual(context["requested_query"], DISCOVERY["query"])
        self.assertEqual(context["requested_criteria"], criteria)
        identity = self.retrieval.resolve.call_args.args[0]["discovered"]
        _, _, draft, result = self.store.finish_discovery.call_args.args
        for data in (identity, draft, result):
            self.assertNotIn("requested_query", data)
            self.assertNotIn("requested_criteria", data)
        self.store.fail.assert_not_called()

    def test_discovery_commits_with_revision_and_embedding(self):
        self.retrieval.resolve.return_value = no_match("rev-1")
        self.retrieval.embed_commitment.return_value = [0.25, 0.75]
        self.store.finish_discovery.return_value = {
            "outcome": "COMMITTED",
            "commitment_id": "fixture-new-commitment",
            "run_id": "fixture-new-run",
        }
        self.agent.execute(DISCOVERY)

        fixture = self.researcher.return_value
        self.researcher.assert_called_once_with(DISCOVERY, None)
        identity = self.retrieval.resolve.call_args.args[0]["discovered"]
        self.assertEqual(identity["amount_value"], fixture["draft"]["amount_value"])
        self.assertEqual(identity["term_periodicity"], fixture["draft"]["term_periodicity"])
        self.assertEqual(identity["original_promise"], fixture["draft"]["original_promise"])
        self.assertNotIn("title", identity)
        self.retrieval.embed_commitment.assert_called_once_with(identity["description"])
        self.store.finish_discovery.assert_called_once()
        claim, revision, draft, result = self.store.finish_discovery.call_args.args
        self.assertEqual((claim, revision), (DISCOVERY_CLAIM, "rev-1"))
        self.assertEqual(draft["embedding"], [0.25, 0.75])
        self.assertEqual(result, fixture["result"])
        self.store.fail.assert_not_called()

    def test_discovery_rechecks_and_reuses_existing_commitment(self):
        self.retrieval.resolve.side_effect = [
            no_match("rev-1"),
            {"outcome": "MATCH", "commitment_id": "TG-GOLD-H008"},
        ]
        self.retrieval.embed_commitment.return_value = [0.25, 0.75]
        self.store.finish_discovery.return_value = {"outcome": "RECHECK_REQUIRED"}
        self.agent.execute(DISCOVERY)
        self.assertEqual(self.retrieval.resolve.call_count, 2)
        self.retrieval.embed_commitment.assert_called_once()
        self.store.finish_discovery.assert_called_once()
        self.store.finish_without_run.assert_called_once_with(DISCOVERY_CLAIM, {
            "outcome": "RESOLVED",
            "commitment_id": "TG-GOLD-H008",
            "run_id": None,
        })
        self.store.fail.assert_not_called()

    def test_discovery_retries_write_using_updated_revision(self):
        self.retrieval.resolve.side_effect = [no_match("rev-1"), no_match("rev-2")]
        self.retrieval.embed_commitment.return_value = [0.25, 0.75]
        self.store.finish_discovery.side_effect = [
            {"outcome": "RECHECK_REQUIRED"},
            {"outcome": "COMMITTED", "commitment_id": "new", "run_id": "run"},
        ]
        self.agent.execute(DISCOVERY)
        revisions = [call.args[1] for call in self.store.finish_discovery.call_args_list]
        self.assertEqual(revisions, ["rev-1", "rev-2"])
        self.retrieval.embed_commitment.assert_called_once()
        self.store.fail.assert_not_called()

    def test_discovery_revision_retries_are_bounded(self):
        self.retrieval.resolve.return_value = no_match("rev-changing")
        self.retrieval.embed_commitment.return_value = [0.25, 0.75]
        self.store.finish_discovery.return_value = {"outcome": "RECHECK_REQUIRED"}
        self.agent.execute(DISCOVERY)
        self.assertEqual(self.store.finish_discovery.call_count, 3)
        claim, error = self.store.fail.call_args.args
        self.assertEqual(claim, DISCOVERY_CLAIM)
        self.assertEqual(error["code"], "RESEARCH_UNAVAILABLE")
        self.assertTrue(error["retryable"])

    def test_discovery_duplicate_ambiguity_finishes_without_write(self):
        decision = {"outcome": "AMBIGUOUS", "question": "Construction or operations?"}
        self.retrieval.resolve.return_value = decision
        self.agent.execute(DISCOVERY)
        self.store.finish_without_run.assert_called_once_with(DISCOVERY_CLAIM, decision)
        self.store.finish_discovery.assert_not_called()
        self.retrieval.embed_commitment.assert_not_called()

    def test_discovery_resolver_error_fails_without_write(self):
        self.retrieval.resolve.side_effect = RuntimeError("Judge failed")
        self.agent.execute(DISCOVERY)
        claim, error = self.store.fail.call_args.args
        self.assertEqual(claim, DISCOVERY_CLAIM)
        self.assertEqual(error["code"], "RESOLUTION_UNAVAILABLE")
        self.store.finish_discovery.assert_not_called()
        self.store.finish_without_run.assert_not_called()

    def test_refresh_completes_only_the_claimed_commitment(self):
        context = {"commitment": {"id": "TG-GOLD-H008"}, "organization": None}
        self.store.load_commitment.return_value = context
        self.researcher.return_value = {"result": research_fixture()["result"]}
        self.agent.execute(REFRESH)
        self.store.load_commitment.assert_called_once_with("TG-GOLD-H008")
        self.researcher.assert_called_once_with(REFRESH, context)
        self.store.finish_refresh.assert_called_once_with(REFRESH_CLAIM, self.researcher.return_value["result"])
        self.store.finish_discovery.assert_not_called()
        self.store.fail.assert_not_called()
        self.retrieval.resolve.assert_not_called()

    def test_refresh_provider_failure_leaves_existing_runs_alone(self):
        self.store.load_commitment.return_value = {"commitment": {"id": "TG-GOLD-H008"}}
        self.researcher.side_effect = TimeoutError("The provider is down")
        self.agent.execute(REFRESH)
        self.store.finish_refresh.assert_not_called()
        self.store.finish_without_run.assert_not_called()
        claim, error = self.store.fail.call_args.args
        self.assertEqual(claim, REFRESH_CLAIM)
        self.assertEqual(error["code"], "RESEARCH_UNAVAILABLE")
        self.assertTrue(error["retryable"])

    def test_refresh_requires_live_retrieval_timestamp(self):
        self.store.load_commitment.return_value = {"commitment": {"id": "TG-GOLD-H008"}}
        result = research_fixture()["result"]
        result["evidence"][0]["retrieved_at"] = None
        self.researcher.return_value = {"result": result}
        self.agent.execute(REFRESH)
        self.store.finish_refresh.assert_not_called()
        self.assertEqual(self.store.fail.call_args.args[1]["code"], "RESEARCH_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
