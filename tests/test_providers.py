import io
import json
import math
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from tracegraph.providers import (
    AnthropicClient,
    ClaudeIdentityJudge,
    ClaudeWebResearcher,
    LocalEmbedding,
    ProviderError,
)
from tracegraph.retrieval import InvalidJudgeResponse, Retrieval

_ORIGINAL_URL = "https://transit.example/public/board-decision"
_PROGRESS_URL = "https://transit.example/public/service-bulletin"
_ORIGINAL_TEXT = "Published January 8, 2025. Section 3: The Authority commits to running five overnight shuttles."
_PROGRESS_TEXT = "The Authority began service on three overnight shuttle routes in October."
_JOB = {"kind": "DISCOVER", "query": "What did Transit Authority pledge about overnight shuttles?",
        "claim_token": "must-not-leave-worker", "job_id": "job-1"}


def _message(content, reason="end_turn"):
    return {"type": "message", "role": "assistant", "stop_reason": reason, "content": content}


def _json_message(data):
    return _message([{"type": "text", "text": json.dumps(data)}])


def _search_result(url, title):
    return {"type": "web_search_result", "url": url, "title": title,
            "encrypted_content": "opaque-payload", "page_age": "November 6, 2026"}


def _search_blocks(results):
    return [
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
         "input": {"query": "Transit Authority overnight shuttle board decision"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": results},
    ]


def _cited_block(url, excerpt, text):
    return {"type": "text", "text": text, "citations": [
        {"type": "web_search_result_location", "url": url, "title": "Citation title",
         "encrypted_index": "opaque-index", "cited_text": excerpt},
    ]}


def _search_message():
    return _message([
        *_search_blocks([
            _search_result(_ORIGINAL_URL, "Board Decision"),
            _search_result(_PROGRESS_URL, "Service Bulletin"),
        ]),
        _cited_block(_ORIGINAL_URL, _ORIGINAL_TEXT, "The Board committed to five shuttles."),
        {"type": "text", "text": "\n\n", "citations": None},
        _cited_block(_PROGRESS_URL, _PROGRESS_TEXT, "Three routes are running."),
    ])


def _structured_finding(citation_id="c2"):
    return {"citation_id": citation_id, "status": "Partial implementation",
            "explanation": "Three of five shuttle routes started.", "summary": "Three overnight routes.",
            "locator": "Page 99"}


def _extracted(draft):
    return _json_message({
        "draft": draft,
        "verdict": "Partially implemented",
        "reasoning": "The service bulletin records three routes compared with the five-route commitment.",
        "findings": [_structured_finding()],
    })


def _draft(citation_id="c1"):
    return {"title": "Run five overnight shuttle routes", "description": "The Transit Authority committed to running five overnight shuttle routes.",
            "organization_name": "Transit Authority", "responsible_entity_raw": "The Authority",
            "beneficiary": {"category": "riders", "name": ""}, "obligation_type": "Service",
            "amount_value": "five routes", "term_periodicity": "overnight", "geographic_scope": "",
            "condition_contingency": "", "milestone": "", "notes": "",
            "original_citation_id": citation_id, "original_locator": "Section 3"}


class ProviderTests(unittest.TestCase):
    def test_messages_headers_and_configured_model(self):
        opener = Mock(return_value=io.BytesIO(json.dumps(_json_message({})).encode()))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder-for-test",
                                    "ANTHROPIC_MODEL": "configured-model"}):
            client = AnthropicClient(opener=opener)
        client.create(system="System", messages=[{"role": "user", "content": "Data"}], max_tokens=128)
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "placeholder-for-test")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(json.loads(request.data)["model"], "configured-model")
        self.assertNotIn("placeholder-for-test", request.data.decode())

    def test_http_error_surfaces_status_without_provider_body(self):
        error = HTTPError("https://api.anthropic.com/v1/messages", 401, "test", {},
                          io.BytesIO(b"response may contain sensitive details"))
        client = AnthropicClient(api_key="placeholder-for-test", opener=Mock(side_effect=error))
        with self.assertRaisesRegex(ProviderError, "^Claude Messages returned HTTP 401$"):
            client.create(system="System", messages=[], max_tokens=128)

    def test_experiment_records_usage_and_missing_usage_without_provider_content(self):
        message = {**_search_message(), "id": "msg-demo",
                   "usage": {"input_tokens": 41, "output_tokens": 9, "server_tool_use": {"web_search_requests": 1}}}
        error = HTTPError("https://api.anthropic.com/v1/messages", 429, "test", {"request-id": "req-rate-limit"}, None)
        opener = Mock(side_effect=[io.BytesIO(json.dumps(message).encode()), error])
        events = []
        client = AnthropicClient("placeholder-for-test", opener=opener, emit=events.append)
        client.create(system="System", messages=[], max_tokens=128, tools=[{"type": "web_search_20250305"}])
        with self.assertRaisesRegex(ProviderError, "HTTP 429"):
            client.create(system="System", messages=[], max_tokens=128)
        self.assertEqual(events[0]["usage"], message["usage"])
        self.assertEqual(events[0]["web_search_ids"], ["srvtoolu_1"])
        self.assertEqual(events[0]["request_id"], "msg-demo")
        self.assertEqual(events[1]["status"], "http_429")
        self.assertIsNone(events[1]["usage"])
        self.assertNotIn("opaque-payload", json.dumps(events))

    def test_experiment_blocks_tools_in_replay_and_calls_past_deadline(self):
        opener = Mock()
        replay = AnthropicClient("placeholder-for-test", opener=opener, allow_web_search=False)
        with self.assertRaisesRegex(ProviderError, "disabled during fixed-evidence replay"):
            replay.create(system="System", messages=[], max_tokens=128, tools=[{"name": "web_search"}])
        expired = AnthropicClient("placeholder-for-test", opener=opener, deadline=0)
        with self.assertRaisesRegex(ProviderError, "reached its deadline"):
            expired.create(system="System", messages=[], max_tokens=128)
        opener.assert_not_called()

    def test_identity_uses_structured_output_and_retrieval_rejects_forged_id(self):
        client = Mock()
        client.create.return_value = _json_message({
            "outcome": "MATCH", "commitment_id": "forged-id", "question": "",
        })
        snapshot = Mock(catalog_revision="r1")
        snapshot.scan.return_value = [{"commitment_id": "real-id", "description": "run buses"}]
        snapshot.vector_top_k.return_value = [{"commitment_id": "real-id", "rank": 1}]
        catalog = Mock()
        catalog.with_snapshot.side_effect = lambda callback: callback(snapshot)
        retrieval = Retrieval(catalog, LocalEmbedding(), ClaudeIdentityJudge(client))
        with self.assertRaises(InvalidJudgeResponse):
            retrieval.resolve({"query": "run buses"})

        request = client.create.call_args.kwargs
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(json.loads(request["messages"][0]["content"])["identity"], {"query": "run buses"})
        self.assertIn("untrusted data", request["system"])

    def test_structured_refusal_does_not_turn_into_no_match(self):
        client = Mock()
        client.create.return_value = _message([{"type": "text", "text": "Refusal"}], "refusal")
        with self.assertRaises(ProviderError):
            ClaudeIdentityJudge(client)({"query": "run buses"}, [])

    def test_discovery_persists_only_verified_source_urls_and_literal_excerpts(self):
        client = Mock()
        client.create.side_effect = [_search_message(), _extracted(_draft())]
        clock = Mock(side_effect=[
            datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc),
            datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc),
        ])
        response = ClaudeWebResearcher(client, clock=clock)(_JOB, None)
        draft = response["draft"]
        original = draft["original_promise"]
        self.assertEqual(original["source"]["url"], _ORIGINAL_URL)
        self.assertEqual(original["source"]["title"], "Board Decision")
        self.assertEqual(original["source"]["published_on"], "2025-01-08")
        self.assertEqual(original["excerpt"], _ORIGINAL_TEXT)
        self.assertEqual(original["locator"], "Section 3")
        self.assertEqual(draft["original_text"], _ORIGINAL_TEXT)
        self.assertEqual(draft["beneficiary"], {"category": "riders", "name": None})

        result = response["result"]
        self.assertEqual(result["verification_date"], "2026-11-08")
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(result["evidence"][0]["status"], "Commitment documented")
        progress = result["evidence"][1]
        self.assertEqual(progress["source"]["url"], _PROGRESS_URL)
        self.assertIsNone(progress["source"]["published_on"])
        self.assertEqual(progress["excerpt"], _PROGRESS_TEXT)
        self.assertEqual(progress["retrieved_at"], "2026-11-07T23:59:00+00:00")
        self.assertEqual(progress["locator"], "Cited passage")
        search, extract = [call.kwargs for call in client.create.call_args_list]
        self.assertEqual(search["tools"][0]["type"], "web_search_20250305")
        self.assertNotIn("output_config", search)
        self.assertIn("output_config", extract)
        self.assertNotIn("tools", extract)
        self.assertNotIn(_JOB["claim_token"], search["messages"][0]["content"])
        self.assertNotIn("opaque-payload", extract["messages"][0]["content"])

    def test_pause_continuation_echoes_tool_blocks_and_resolves_prior_result(self):
        client = Mock()
        initial = _search_message()["content"][:2]
        continued = _search_message()["content"][2:]
        client.create.side_effect = [_message(initial, "pause_turn"), _message(continued), _extracted(None)]
        response = ClaudeWebResearcher(client)({"kind": "REFRESH", "job_id": "job-2"},
                                             {"commitment": {"description": "Run five routes."}})
        self.assertEqual(client.create.call_args_list[1].kwargs["messages"][1],
                         {"role": "assistant", "content": initial})
        self.assertNotIn("draft", response)
        self.assertEqual(response["result"]["evidence"][0]["source"]["url"], _PROGRESS_URL)

    def test_experiment_strategy_wraps_only_discovery_and_keeps_extraction(self):
        client = Mock()
        client.create.side_effect = [_extracted(_draft()), _search_message(), _extracted(None)]
        citations = {
            "c1": {"url": _ORIGINAL_URL, "title": "Board Decision", "excerpt": _ORIGINAL_TEXT, "context": ""},
            "c2": {"url": _PROGRESS_URL, "title": "Service Bulletin", "excerpt": _PROGRESS_TEXT, "context": ""},
        }
        strategy = Mock(return_value=("Cited fixture brief", citations))
        events = []
        researcher = ClaudeWebResearcher(client, search_strategy=strategy, emit=events.append)
        response = researcher(_JOB, None)
        strategy.assert_called_once_with(researcher, {"kind": "DISCOVER", "target": _JOB["query"]},
                                         emit=events.append)
        self.assertEqual(response["draft"]["original_text"], _ORIGINAL_TEXT)
        self.assertEqual([event["type"] for event in events],
                         ["research_start", "research_capture", "research_extracted"])
        self.assertEqual(events[1]["citations"], citations)
        researcher({"kind": "REFRESH"}, {"commitment": {"description": "Run five routes."}})
        strategy.assert_called_once()
        self.assertIn("tools", client.create.call_args_list[1].kwargs)

    def test_refresh_only_sends_public_identity_and_provenance(self):
        client = Mock()
        client.create.side_effect = [_search_message(), _extracted(None)]
        commitment = {
            "id": "internal-commitment-id",
            "description": "Run five overnight shuttle routes",
            "original_text": _ORIGINAL_TEXT,
            "geographic_scope": "Greater Transit District",
            "term_periodicity": "Overnight",
            "notes": "private workbook annotation",
            "eval_labels": ["internal evaluation"],
            "beneficiary": {"category": "riders", "annotator": "annotator handle"},
            "original_promise": {
                "document_id": "internal-document-id",
                "locator": "Section 3",
                "char_start": 41,
                "source": {"id": "internal-source-id", "url": _ORIGINAL_URL,
                           "title": "Board Decision", "citation": "Board agenda item 8",
                           "publisher": "Transit Authority", "published_on": "2025-01-08",
                           "notes": "internal source notes"},
            },
        }
        context = {
            "commitment": commitment,
            "organization": {"id": "internal-organization-id", "name": "Transit Authority"},
            "previous_runs": [{"reasoning": "internal run details"}],
        }
        ClaudeWebResearcher(client)({"kind": "REFRESH", "claim_token": "internal worker token"}, context)

        expected_target = {
            "description": "Run five overnight shuttle routes",
            "original_text": _ORIGINAL_TEXT,
            "geographic_scope": "Greater Transit District",
            "term_periodicity": "Overnight",
            "organization_name": "Transit Authority",
            "beneficiary": {"category": "riders"},
            "original_promise": {
                "locator": "Section 3",
                "source": {"url": _ORIGINAL_URL, "title": "Board Decision",
                           "citation": "Board agenda item 8", "publisher": "Transit Authority",
                           "published_on": "2025-01-08"},
            },
        }
        for call in client.create.call_args_list:
            body = json.loads(call.kwargs["messages"][0]["content"])
            self.assertEqual(body["target"], expected_target)
            self.assertNotIn("claim_token", body)

    def test_refresh_omits_original_text_without_public_url(self):
        client = Mock()
        client.create.return_value = _message(_search_blocks([]))
        context = {"commitment": {
            "description": "Run five overnight shuttle routes",
            "original_text": "unlinked draft excerpt",
            "responsible_entity_raw": "Transit Authority",
            "original_promise": {"source": {"title": "Unlinked document"}},
        }}
        with self.assertRaisesRegex(ProviderError, "no cited evidence"):
            ClaudeWebResearcher(client)({"kind": "REFRESH"}, context)
        body = json.loads(client.create.call_args.kwargs["messages"][0]["content"])
        self.assertNotIn("original_text", body["target"])
        self.assertEqual(body["target"]["organization_name"], "Transit Authority")


    def test_clean_empty_discovery_is_distinct_from_tool_error(self):
        client = Mock()
        client.create.return_value = _message(_search_blocks([]))
        self.assertEqual(ClaudeWebResearcher(client)(_JOB, None), {"draft": None})
        self.assertEqual(client.create.call_count, 1)
        client.create.return_value = _message(_search_blocks({
            "type": "web_search_tool_result_error", "error_code": "too_many_requests",
        }))
        with self.assertRaises(ProviderError):
            ClaudeWebResearcher(client)(_JOB, None)

    def test_citation_absent_from_search_results_is_rejected(self):
        response = _search_message()
        response["content"][-1]["citations"][0]["url"] = "https://invented.example/unsupported"
        client = Mock()
        client.create.return_value = response
        with self.assertRaisesRegex(ProviderError, "absent from the web search results"):
            ClaudeWebResearcher(client)(_JOB, None)
        self.assertEqual(client.create.call_count, 1)

    def test_original_promise_cannot_select_invented_citation(self):
        client = Mock()
        client.create.side_effect = [_search_message(), _extracted(_draft("c999"))]
        with self.assertRaisesRegex(ProviderError, "unknown citation ID"):
            ClaudeWebResearcher(client)(_JOB, None)

    def test_search_omission_and_unusable_results_are_failures(self):
        client = Mock()
        for content in ([{"type": "text", "text": "No evidence."}],
                        _search_blocks([_search_result(_ORIGINAL_URL, "Board Decision")])):
            with self.subTest(content=content):
                client.create.return_value = _message(content)
                with self.assertRaises(ProviderError):
                    ClaudeWebResearcher(client)(_JOB, None)

    def test_research_extraction_failure_does_not_turn_into_no_public_promise(self):
        client = Mock()
        extraction = _extracted(None)
        answer = json.loads(extraction["content"][0]["text"])
        answer["findings"][0]["citation_id"] = "c999"
        client.create.side_effect = [_search_message(), _json_message(answer)]
        with self.assertRaisesRegex(ProviderError, "unknown citation ID"):
            ClaudeWebResearcher(client)(_JOB, None)

    def test_empty_refresh_cannot_replace_previous_verdict(self):
        client = Mock()
        client.create.return_value = _message(_search_blocks([]))
        with self.assertRaisesRegex(ProviderError, "no cited evidence"):
            ClaudeWebResearcher(client)({"kind": "REFRESH"}, {"commitment": {}})

    def test_local_embedding_is_normalized_repeatable_and_distinguishes_content(self):
        embedding = LocalEmbedding()
        target = embedding("Bally’s funds the police substation and annual safety support")
        same = embedding("BALLY'S funds the police substation and annual safety support")
        similar = embedding("Ballys commits to annual support for the police substation")
        unrelated = embedding("Operate a renewable electricity procurement program")
        self.assertEqual(embedding.profile, "tracegraph-hash-v1-384")
        self.assertEqual(len(target), 384)
        self.assertEqual(target, same)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in target)), 1.0)
        self.assertGreater(sum(a * b for a, b in zip(target, similar)),
                           sum(a * b for a, b in zip(target, unrelated)))


if __name__ == "__main__":
    unittest.main()
