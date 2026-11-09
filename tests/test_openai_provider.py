import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from tests.test_providers import _coverage_row, _draft, _JOB, _ORIGINAL_TEXT, _ORIGINAL_URL, _PROGRESS_TEXT, _PROGRESS_URL
from tracegraph.experimental_prompt import search
from tracegraph.openai_provider import OpenAIClient, OpenAIWebResearcher
from tracegraph.providers import ClaudeIdentityJudge, ProviderError


_CANDIDATES = [{"commitment_id": "different-pledge", "description": "A separate public pledge."}]


def _tool(sources=None, *, status="completed"):
    action = {"type": "search"}
    if sources is not None:
        action["sources"] = [{"type": "url", "url": url} for url in sources]
    return {"type": "web_search_call", "id": "ws_test", "status": status, "action": action}


def _annotation(url, title):
    return {"type": "url_citation", "url": url, "title": title, "start_index": 0, "end_index": 7}


def _response(data, *, tools=(), annotations=(), status="completed", refusal=False):
    part = {"type": "refusal", "refusal": "Refused fixture"} if refusal else {
        "type": "output_text", "text": json.dumps(data), "annotations": list(annotations),
    }
    return io.BytesIO(json.dumps({
        "object": "response", "id": "resp_test", "status": status,
        "output": [*tools, {"type": "message", "role": "assistant", "content": [part]}],
        "usage": {"input_tokens": 20, "output_tokens": 30},
    }).encode())


def _proposed(*pairs, stop=""):
    return {"passages": [{"url": url, "quote": quote} for url, quote in pairs], "stop_reason": stop}


def _extracted(draft, citation_id, *, coverage=None):
    result = {
        "draft": draft, "verdict": "Partial implementation", "reasoning": "Three routes opened.",
        "findings": [{"citation_id": citation_id, "status": "Partial implementation",
                      "explanation": "Three routes opened.", "summary": "", "locator": ""}],
    }
    if draft is not None or coverage is not None:
        result["criteria"] = coverage if coverage is not None else [_coverage_row()]
    return result


class OpenAIProviderTests(unittest.TestCase):
    def test_shared_identity_judge_uses_strict_responses_format(self):
        answer = {"outcome": "NO_MATCH", "commitment_id": "", "question": ""}
        opener = Mock(return_value=_response(answer))
        events = []
        client = OpenAIClient("test-key", "test-model", opener=opener, emit=events.append)

        self.assertEqual(ClaudeIdentityJudge(client)({"query": "Public query"}, _CANDIDATES), answer)

        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "test-model")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["max_output_tokens"], 1024)
        self.assertNotIn("tools", payload)
        self.assertNotIn("test-key", request.data.decode())
        format_spec = payload["text"]["format"]
        self.assertEqual(format_spec["type"], "json_schema")
        self.assertEqual(format_spec["name"], "tracegraph")
        self.assertTrue(format_spec["strict"])
        self.assertEqual(format_spec["schema"]["required"], list(answer))
        self.assertEqual(events[0]["usage"], {"input_tokens": 20, "output_tokens": 30})
        self.assertNotIn("Public query", json.dumps(events))

    def test_refusal_incomplete_and_http_errors_are_provider_failures(self):
        http_error = HTTPError("https://api.openai.com/v1/responses", 429, "test", {},
                               io.BytesIO(b"provider body with private details"))
        opener = Mock(side_effect=[_response({}, refusal=True), _response({}, status="incomplete"), http_error])
        judge = ClaudeIdentityJudge(OpenAIClient("test-key", opener=opener))
        for expected in ("refused", "did not complete", "^OpenAI Responses returned HTTP 429$"):
            with self.subTest(expected=expected), self.assertRaisesRegex(ProviderError, expected):
                judge({"query": "Public query"}, _CANDIDATES)

    def test_discovery_forwards_verified_quotes_and_records_stop_marker_separately(self):
        false_quote = "All six shuttle routes began two months early."
        forged_url = "https://transit.example/forged"
        proposals = _proposed(
            (_ORIGINAL_URL, _ORIGINAL_TEXT.replace(" Section", "\nSection")),
            (_ORIGINAL_URL, false_quote), (forged_url, _ORIGINAL_TEXT), (_PROGRESS_URL, _PROGRESS_TEXT),
            stop="BUDGET",
        )
        opener = Mock(side_effect=[
            _response(proposals, tools=[_tool([_PROGRESS_URL])],
                      annotations=[_annotation(_ORIGINAL_URL, "Board Decision")]),
            _response(_extracted(_draft(), "c2")),
            _response({"checks": [{"criterion_id": "1", "explanation": "Checked the Board Decision.",
                                   "documented": True}]}),
        ])
        source = Mock(side_effect={_ORIGINAL_URL: _ORIGINAL_TEXT, _PROGRESS_URL: _PROGRESS_TEXT}.get)
        events = []
        researcher = OpenAIWebResearcher(
            OpenAIClient("test-key", opener=opener, emit=events.append),
            source_loader=lambda url, **_: source(url), search_strategy=search, emit=events.append,
        )

        result = researcher(_JOB, None)

        self.assertEqual(result["draft"]["original_text"], _ORIGINAL_TEXT)
        self.assertEqual(result["draft"]["original_promise"]["source"]["title"], "Board Decision")
        self.assertEqual([item["excerpt"] for item in result["result"]["evidence"]],
                         [_ORIGINAL_TEXT, _PROGRESS_TEXT])
        self.assertEqual([call.args for call in source.call_args_list], [(_ORIGINAL_URL,), (_PROGRESS_URL,)])
        search_payload, extraction_payload, scope_payload = [
            json.loads(call.args[0].data) for call in opener.call_args_list
        ]
        self.assertEqual(search_payload["tools"], [{"type": "web_search"}])
        self.assertEqual(search_payload["tool_choice"], "required")
        self.assertEqual(search_payload["max_tool_calls"], 6)
        self.assertEqual(search_payload["include"], ["web_search_call.action.sources"])
        self.assertNotIn(_JOB["claim_token"], json.dumps(search_payload))
        self.assertNotIn("tools", extraction_payload)
        self.assertNotIn(false_quote, json.dumps(extraction_payload))
        self.assertNotIn(forged_url, json.dumps(extraction_payload))
        self.assertNotIn("STOP_REASON=BUDGET", json.dumps(extraction_payload))
        self.assertNotIn("tools", scope_payload)
        self.assertTrue(any(event.get("stop_reason") == "BUDGET" for event in events))
        self.assertTrue(any(event.get("web_search_ids") == ["ws_test"] for event in events))

    def test_refresh_uses_verified_search_without_discovery_strategy(self):
        opener = Mock(side_effect=[
            _response(_proposed((_PROGRESS_URL, _PROGRESS_TEXT)), tools=[_tool([_PROGRESS_URL])]),
            _response(_extracted(None, "c1")),
        ])
        strategy = Mock()
        researcher = OpenAIWebResearcher(
            OpenAIClient("test-key", opener=opener),
            source_loader=Mock(return_value=_PROGRESS_TEXT), search_strategy=strategy,
        )

        result = researcher({"kind": "REFRESH"}, {"commitment": {"description": "Run five routes."}})

        strategy.assert_not_called()
        self.assertEqual(set(result), {"result"})
        self.assertEqual(result["result"]["evidence"][0]["source"]["url"], _PROGRESS_URL)
        payload = json.loads(opener.call_args_list[0].args[0].data)
        self.assertEqual(json.loads(payload["input"][0]["content"])["kind"], "REFRESH")

    def test_verified_scope_gap_blocks_a_narrower_draft_despite_search_stopping(self):
        false_quote = "The Authority promised to add three bus stops."
        opener = Mock(side_effect=[
            _response(_proposed((_ORIGINAL_URL, _ORIGINAL_TEXT), (_PROGRESS_URL, false_quote),
                                stop="SUFFICIENT"), tools=[_tool([_ORIGINAL_URL, _PROGRESS_URL])]),
            _response(_extracted(_draft(), "c1", coverage=[
                _coverage_row(delivery=()),
                _coverage_row("Add three bus stops", promise=(), delivery=()),
            ])),
        ])
        events = []
        researcher = OpenAIWebResearcher(
            OpenAIClient("test-key", opener=opener),
            source_loader=lambda url, **_: {_ORIGINAL_URL: _ORIGINAL_TEXT, _PROGRESS_URL: _PROGRESS_TEXT}[url],
            search_strategy=search, emit=events.append,
        )

        job = {**_JOB, "query": "What did the authority promise about overnight shuttle routes and new bus stops?"}
        self.assertEqual(researcher(job, None), {"draft": None, "missing_scope": ["Add three bus stops"]})
        self.assertTrue(any(event.get("stop_reason") == "SUFFICIENT" for event in events))
        extraction_payload = json.loads(opener.call_args_list[1].args[0].data)
        self.assertNotIn(false_quote, json.dumps(extraction_payload))
        self.assertNotIn("STOP_REASON", json.dumps(extraction_payload))

    def test_explicit_empty_results_are_distinct_from_failed_or_unverifiable_search(self):
        bad_quote = "Model-written words without a source match."
        cases = (
            (_response(_proposed(), tools=[_tool([])]), None),
            (_response(_proposed(), tools=[_tool()]), "did not report"),
            (_response(_proposed()), "did not complete"),
            (_response(_proposed(), tools=[_tool([], status="failed")]), "did not complete"),
            (_response(_proposed(), tools=[_tool([_ORIGINAL_URL])]), "without proposing evidence"),
            (_response(_proposed((_ORIGINAL_URL, bad_quote)), tools=[_tool([_ORIGINAL_URL])]), "could not be verified"),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                client = OpenAIClient("test-key", opener=Mock(return_value=response))
                researcher = OpenAIWebResearcher(client, source_loader=Mock(return_value=_ORIGINAL_TEXT))
                if expected is None:
                    self.assertEqual(researcher(_JOB, None), {"draft": None})
                else:
                    with self.assertRaisesRegex(ProviderError, expected):
                        researcher(_JOB, None)

    def test_provider_limits_block_search_before_sending_request(self):
        opener = Mock()
        for config, expected in (({"allow_web_search": False}, "disabled"), ({"deadline": 0}, "deadline")):
            with self.subTest(expected=expected):
                client = OpenAIClient("test-key", opener=opener, **config)
                with self.assertRaisesRegex(ProviderError, expected):
                    client.search({"kind": "DISCOVER"}, system="Research", max_uses=1)
        opener.assert_not_called()
