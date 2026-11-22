import json
import os
from unittest import TestCase, skipUnless
from unittest.mock import Mock

from tests.test_agent import DISCOVERY, research_fixture
from tests.test_providers import (_coverage_row, _draft, _extracted, _json_message, _scope_review, _target_review,
                                 _ORIGINAL_TEXT, _ORIGINAL_URL, _structured_finding, _search_message)
from tracegraph.agent import Agent
from tracegraph.providers import ClaudeWebResearcher, ProviderError


SHORT = "The Authority commits to running five overnight shuttles ..."
FULL = "The Authority commits to running five overnight shuttles every weekday."
CAPTURE = {"c1": {"url": _ORIGINAL_URL, "title": "Actual board document", "excerpt": SHORT,
                  "context": "Generated assertion of a different program, never source evidence."}}
JOB = {**DISCOVERY, "query": "Has the Transit Authority delivered five overnight shuttles every weekday?"}


def extraction(*, draft=None, gap="Weekday term missing.", recovery=(), followup="", status="IDENTIFIED",
               question="", relevant=("c1",), promise=("c1",)):
    answer = json.loads(_extracted(draft, coverage=[_coverage_row("Run five overnight shuttles every weekday",
                                    promise=promise, delivery=(), promise_gap=gap)] )["content"][0]["text"])
    answer.update(target_status=status, clarification_question=question, relevant_citation_ids=list(relevant),
                  recovery_citation_ids=list(recovery), followup_query=followup,
                  findings=[_structured_finding(id_) for id_ in relevant])
    return _json_message(answer)


def complete_review(id_="c1", *, final=True):
    return _json_message({**({"target_status": "IDENTIFIED", "clarification_question": "", "alternative_citation_ids": [], "accepted_finding_ids": ["f1"]} if final else {}),
                         "checks": [{"criterion_id": "1", "explanation": "Original words document the condition.",
                                      "original_citation_ids": [id_], "missing_original_terms": []}]})


class DiscoveryRecoveryTests(TestCase):
    def researcher(self, responses, *, loader=None, capture=None):
        client = Mock()
        client.create.side_effect = responses
        events = []
        researcher = ClaudeWebResearcher(client, source_loader=loader or Mock(side_effect=OSError("Unavailable")),
            search_strategy=lambda *args, **kwargs: ("Untrusted generated brief", capture or CAPTURE), emit=events.append)
        return researcher, client, events

    def test_short_quote_recovers_literal_original_and_does_not_require_delivery(self):
        draft = _draft("c2")
        loader = Mock(return_value="Report begins. " + FULL + " Report ends.")
        researcher, client, events = self.researcher([
            extraction(recovery=("c1",)), _scope_review(False, final=False),
            _json_message({"passages": [{"document_id": "d1", "quote": FULL}]}),
            extraction(draft=draft, gap="", promise=("c2",), relevant=("c2",)), complete_review("c2"), _target_review(),
        ], loader=loader)
        result = researcher(JOB, None)
        self.assertEqual(result["draft"]["original_text"], FULL)
        self.assertEqual(result["draft"]["original_promise"]["source"]["url"], _ORIGINAL_URL)
        loader.assert_called_once_with(_ORIGINAL_URL, timeout=8)
        for call in client.create.call_args_list:
            self.assertNotIn(CAPTURE["c1"]["context"], call.kwargs["messages"][0]["content"])
        self.assertTrue(any(row.get("type") == "research_recovery_capture" for row in events))

    def test_inaccessible_original_preserves_quote_and_gap_without_question(self):
        researcher, _, events = self.researcher([extraction(recovery=("c1",)), _scope_review(False, final=False)])
        result = researcher(JOB, None)
        self.assertIsNone(result["draft"])
        self.assertEqual(result["target_status"], "IDENTIFIED")
        self.assertEqual(result["clarification_question"], "")
        self.assertIn(SHORT, [item["excerpt"] for item in result["related_evidence"]])
        self.assertTrue(any(row.get("status") == "UNAVAILABLE" for row in events))

    def test_sufficient_original_terms_get_rechecked_after_false_missing_scope(self):
        researcher, _, _ = self.researcher([
            extraction(), complete_review(final=False), extraction(draft=_draft(), gap=""), complete_review(), _target_review(),
        ], capture={"c1": {**CAPTURE["c1"], "excerpt": FULL}})
        self.assertIsNotNone(researcher(JOB, None)["draft"])

    def test_review_output_limit_retains_all_sixteen_verified_excerpts(self):
        capture = {f"c{i}": {"url": f"https://example.org/source/{i}", "title": f"Source {i}",
                             "excerpt": f"Verified source excerpt number {i} about the same proposed project."}
                   for i in range(1, 17)}
        truncated = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}
        researcher, _, events = self.researcher([extraction(draft=_draft(), gap=""), truncated], capture=capture)
        result = researcher(JOB, None)
        self.assertIsNone(result["draft"])
        self.assertEqual(len(result["related_evidence"]), 1)
        self.assertEqual(len(result["unmatched_evidence"]), 15)
        self.assertTrue(any(row.get("stage") == "original_terms_review" for row in events))

    def test_second_extraction_output_limit_keeps_recovered_literal(self):
        researcher, _, _ = self.researcher([
            extraction(recovery=("c1",)), _scope_review(False, final=False),
            _json_message({"passages": [{"document_id": "d1", "quote": FULL}]}),
            {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]},
        ], loader=Mock(return_value=FULL))
        result = researcher(JOB, None)
        self.assertIn(FULL, [row["excerpt"] for row in result["unmatched_evidence"]])
        self.assertNotIn(FULL, [row["excerpt"] for row in result["related_evidence"]])
        self.assertNotIn("clarification_question", result)

    def test_source_passage_selection_failure_keeps_original_quote(self):
        researcher, _, events = self.researcher([
            extraction(recovery=("c1",)), _scope_review(False, final=False),
            {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]},
        ], loader=Mock(return_value=FULL))
        result = researcher(JOB, None)
        self.assertIn(SHORT, [row["excerpt"] for row in result["related_evidence"]])
        self.assertTrue(any(row.get("stage") == "source_passage_selection" for row in events))

    def test_recovery_cannot_invent_words_not_in_the_fetched_document(self):
        researcher, _, _ = self.researcher([
            extraction(recovery=("c1",)), _scope_review(False, final=False),
            _json_message({"passages": [{"document_id": "d1", "quote": "All five routes actually opened in January."}]}),
        ], loader=Mock(return_value=FULL))
        result = researcher(JOB, None)
        self.assertIsNone(result["draft"])
        self.assertNotIn("actually opened", json.dumps(result))

    def test_recovery_failure_falls_back_to_equivalent_search_within_remaining_budget(self):
        researcher, _, _ = self.researcher([
            extraction(recovery=("c1",), followup="The original board terms including operating days"), _scope_review(False, final=False),
            extraction(draft=_draft("c2"), gap="", relevant=("c2",), promise=("c2",)), complete_review("c2"), _target_review(),
        ])
        original_strategy = researcher._search_strategy
        def initial(*args, **kwargs):
            researcher._remaining_search_uses = 2
            return original_strategy(*args, **kwargs)
        researcher._search_strategy = initial
        def followup(request, **options):
            self.assertEqual(researcher._claim_search_uses(options["max_uses"]), 2)
            return "", {"found": {**CAPTURE["c1"], "excerpt": FULL}}
        researcher._search = Mock(side_effect=followup)
        result = researcher(JOB, None)
        self.assertEqual(result["draft"]["original_text"], FULL)
        self.assertEqual(researcher._remaining_search_uses, 0)
        self.assertEqual(researcher._search.call_count, 1)

    def test_unrelated_evidence_is_not_relabelled_as_the_precise_target(self):
        response = extraction(status="NOT_LOCATED", relevant=())
        data = json.loads(response["content"][0]["text"])
        data["findings"] = [_structured_finding("c1")]
        response = _json_message(data)
        researcher, _, _ = self.researcher([response, _scope_review(False, final=False)])
        result = researcher(JOB, None)
        self.assertEqual(result["target_status"], "NOT_LOCATED")
        self.assertEqual(result["related_evidence"], [])
        self.assertEqual(result["unmatched_evidence"][0]["excerpt"], SHORT)

    def test_not_located_status_cannot_also_assert_target_facts(self):
        researcher, _, _ = self.researcher([extraction(status="NOT_LOCATED", relevant=("c1",)), _scope_review(False, final=False)])
        result = researcher(JOB, None)
        self.assertEqual(result["related_evidence"], [])
        self.assertTrue(result["unmatched_evidence"])

    def test_failure_before_relevance_review_keeps_excerpts_unmatched(self):
        researcher, _, _ = self.researcher([
            {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]},
        ])
        result = researcher(JOB, None)
        self.assertEqual(result["related_evidence"], [])
        self.assertEqual(result["unmatched_evidence"][0]["excerpt"], SHORT)

    def test_refresh_keeps_its_existing_conflict_review_search_allowance(self):
        client = Mock()
        client.create.side_effect = [_search_message(), _extracted(None), _search_message(),
                                     _json_message({"findings": [_structured_finding("c1")]})]
        researcher = ClaudeWebResearcher(client)
        researcher({"kind": "REFRESH"}, {"commitment": {"description": "Run five routes"}})
        researcher.review_conflicts({"commitment": {"description": "Run five routes"}}, [])
        self.assertEqual([call.kwargs["tools"][0]["max_uses"] for call in client.create.call_args_list
                          if "tools" in call.kwargs], [6, 3])

    def test_independent_review_stops_extractor_choosing_one_of_two_real_objects(self):
        reviewed = json.loads(complete_review()["content"][0]["text"])
        reviewed.update(target_status="AMBIGUOUS", clarification_question="Which of the two funds do you mean?",
                        alternative_citation_ids=["c1", "c2"])
        capture = {**CAPTURE, "c2": {**CAPTURE["c1"], "excerpt": "The Authority commits to a separate annual training fund."}}
        researcher, client, _ = self.researcher([
            extraction(draft=_draft(), gap=""), _json_message(reviewed),
        ], capture=capture)
        result = researcher(JOB, None)
        self.assertIsNone(result["draft"])
        self.assertEqual(result["target_status"], "AMBIGUOUS")
        self.assertIn("two funds", result["clarification_question"])
        self.assertEqual(len(result["related_evidence"]), 2)
        final_input = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        self.assertIn("c2", final_input["citations"])
        self.assertEqual(final_input["requested_query"], JOB["query"])

    def test_review_rejects_claim_attached_to_another_clause_at_same_url(self):
        reviewed = json.loads(complete_review()["content"][0]["text"])
        reviewed["accepted_finding_ids"] = []
        capture = {"c1": {**CAPTURE["c1"], "excerpt": FULL},
                   "c2": {**CAPTURE["c1"], "excerpt": "The Authority approved a different training fund for ten years."}}
        researcher, _, _ = self.researcher([
            extraction(draft=_draft(), gap="", relevant=("c2",)), _json_message(reviewed), _target_review(),
        ], capture=capture)
        result = researcher(JOB, None)
        self.assertIsNotNone(result["draft"])
        self.assertEqual([row["excerpt"] for row in result["result"]["evidence"]], [FULL])

    def test_review_cannot_accept_a_finding_id_outside_the_supplied_proposals(self):
        reviewed = json.loads(complete_review()["content"][0]["text"])
        reviewed["accepted_finding_ids"] = ["invented-finding"]
        researcher, _, _ = self.researcher([extraction(draft=_draft(), gap=""), _json_message(reviewed)])
        result = researcher(JOB, None)
        self.assertIsNone(result["draft"])
        self.assertIn(SHORT, [row["excerpt"] for row in result["related_evidence"]])

    def test_finding_check_sees_only_its_own_quote_and_rejects_cross_clause_leak(self):
        reviewed = json.loads(complete_review()["content"][0]["text"])
        capture = {"c1": {**CAPTURE["c1"], "excerpt": FULL},
                   "c2": {**CAPTURE["c1"], "excerpt": "The Authority approved a different training fund for ten years."}}
        researcher, client, events = self.researcher([
            extraction(draft=_draft(), gap="", relevant=("c2",)), _json_message(reviewed), _target_review(),
            _json_message({"supported": False, "quoted_support": "", "explanation": "The timing and shuttle claim are absent from this training fund quote."}),
        ], capture=capture)
        result = researcher(JOB, None)
        self.assertEqual([row["excerpt"] for row in result["result"]["evidence"]], [FULL])
        request = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(request["source_excerpt"]["excerpt"], capture["c2"]["excerpt"])
        self.assertNotIn(FULL, json.dumps(request))
        self.assertTrue(any(row.get("type") == "research_finding_review" and not row["accepted"] for row in events))

    def test_only_six_extra_graph_candidates_are_reviewed_and_all_others_retained(self):
        capture = {"c1": {**CAPTURE["c1"], "excerpt": FULL}, **{
            f"c{i}": {**CAPTURE["c1"], "excerpt": f"The Authority discussed unconfirmed option number {i}."}
            for i in range(2, 9)}}
        reviewed = json.loads(complete_review()["content"][0]["text"])
        reviewed["accepted_finding_ids"] = [f"f{i}" for i in range(1, 8)]
        researcher, client, _ = self.researcher([
            extraction(draft=_draft(), gap="", relevant=tuple(f"c{i}" for i in range(2, 9))),
            _json_message(reviewed), _target_review(), *[_json_message({"supported": False, "quoted_support": "",
                                                    "explanation": "Not established by this source."}) for _ in range(6)],
        ], capture=capture)
        result = researcher(JOB, None)
        self.assertEqual(client.create.call_count, 9)
        self.assertEqual(len(result["unmatched_evidence"]), 7)
        self.assertEqual([row["excerpt"] for row in result["result"]["evidence"]], [FULL])

    def test_source_only_target_gate_rejects_a_generated_merged_object(self):
        capture = {**CAPTURE, "c2": {**CAPTURE["c1"], "excerpt": "The Authority promises another annual training fund."}}
        merged = _draft()
        merged["description"] += " It also promises an annual training fund."
        ambiguous = _json_message({"target_status": "AMBIGUOUS", "clarification_question": "Which fund do you mean?",
                                   "alternative_citation_ids": ["c1", "c2"]})
        researcher, client, _ = self.researcher([
            extraction(draft=merged, gap=""), complete_review(), ambiguous,
        ], capture=capture)
        result = researcher(JOB, None)
        self.assertIsNone(result["draft"])
        self.assertEqual(result["target_status"], "AMBIGUOUS")
        request = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(set(request), {"requested_query", "citations"})
        self.assertNotIn(merged["description"], json.dumps(request))
        self.assertEqual(len(result["related_evidence"]), 2)


class PartialResultRoutingTests(TestCase):
    def test_same_url_distinct_excerpts_keep_source_titles_and_all_facts(self):
        fixture = research_fixture()
        source = fixture["draft"]["original_promise"]["source"]
        evidence = [{**fixture["result"]["evidence"][0], "source": source, "excerpt": quote,
                     "summary": "Unsupported claim that the full formula was quoted."}
                    for quote in ("The board proposed a housing fund.", "The board proposed a separate community fund.")]
        store = Mock()
        Agent(Mock(), store, Mock(return_value={"draft": None, "target_status": "IDENTIFIED",
                    "related_evidence": evidence, "gaps": ["The original annual formula is still needed."]})).execute(DISCOVERY)
        result = store.finish_without_run.call_args.args[1]
        self.assertEqual(result["outcome"], "PARTIAL_RESEARCH")
        self.assertEqual(len(result["facts"]), 2)
        self.assertEqual(len(result["suggestions"]), 2)
        self.assertTrue(all(row["title"] == source["title"] for row in result["suggestions"]))
        self.assertNotIn("question", result)
        store.finish_discovery.assert_not_called()

    def test_true_object_ambiguity_keeps_question_and_does_not_save_a_commitment(self):
        store = Mock()
        Agent(Mock(), store, Mock(return_value={"draft": None, "target_status": "AMBIGUOUS",
              "clarification_question": "Do you mean the housing fund or the community fund?"})).execute(DISCOVERY)
        self.assertEqual(store.finish_without_run.call_args.args[1]["outcome"], "AMBIGUOUS")
        store.finish_discovery.assert_not_called()

    def test_unverified_source_links_never_become_facts_or_quotes(self):
        store = Mock()
        Agent(Mock(), store, Mock(return_value={"draft": None, "related_sources": [
            {"title": "Official board report", "source_url": _ORIGINAL_URL}]})).execute(DISCOVERY)
        result = store.finish_without_run.call_args.args[1]
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["suggestions"][0]["excerpt"], "")


@skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL")
class PartialApiIntegrationTests(TestCase):
    def test_successful_graph_keeps_unconfirmed_records_in_the_terminal_job_only(self):
        from tests.db_support import isolated_database
        from tracegraph.runtime import create_application, prepare_catalog
        from tracegraph.runtime_fixtures import FIXTURE_QUERY, fixture_research
        with isolated_database(os.environ["TEST_DATABASE_URL"]) as dsn:
            prepare_catalog(dsn)
            app = create_application(dsn, "fixture")
            def research(job, context):
                return {**fixture_research(job, context), "unmatched_evidence": [
                    {"source": {"title": "Other fund clause", "url": _ORIGINAL_URL},
                     "excerpt": "A separate training fund remains outside this commitment."}]}
            app.agent._researcher = research
            _, _, accepted = app.respond("POST", "/api/v1/lookup", {"Idempotency-Key": "retained"},
                                         json.dumps({"query": FIXTURE_QUERY}).encode())
            app.process_one_job()
            _, _, job = app.respond("GET", f'/api/v1/research-jobs/{accepted["job_id"]}', {})
            result = job["result"]
            self.assertEqual(result["outcome"], "RESOLVED")
            self.assertEqual(len(result["unmatched_records"]), 1)
            _, _, graph = app.respond("GET", f'/api/v1/commitments/{result["commitment_id"]}', {})
            self.assertNotIn("separate training fund", json.dumps(graph))

    def test_normal_lookup_and_poll_return_partial_material_without_fake_commitment(self):
        from tests.db_support import isolated_database
        from tracegraph.runtime import create_application, prepare_catalog
        import psycopg
        with isolated_database(os.environ["TEST_DATABASE_URL"]) as dsn:
            prepare_catalog(dsn)
            app = create_application(dsn, "fixture")
            response = {"draft": None, "target_status": "IDENTIFIED", "related_evidence": [
                {"source": {"title": "Actual board report", "url": _ORIGINAL_URL}, "excerpt": FULL}],
                "gaps": ["A record of actual delivery is not yet available."]}
            app.agent._researcher = Mock(return_value=response)
            status, _, accepted = app.respond("POST", "/api/v1/lookup", {"Idempotency-Key": "partial"},
                                              json.dumps({"query": "Trace this precise board commitment."}).encode())
            self.assertEqual(status, 202)
            app.process_one_job()
            _, _, job = app.respond("GET", f'/api/v1/research-jobs/{accepted["job_id"]}', {})
            self.assertEqual(job["status"], "COMPLETED")
            self.assertEqual(job["result"]["outcome"], "PARTIAL_RESEARCH")
            self.assertEqual(job["result"]["facts"][0]["excerpt"], FULL)
            with psycopg.connect(dsn) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM commitment").fetchone()[0], 0)
