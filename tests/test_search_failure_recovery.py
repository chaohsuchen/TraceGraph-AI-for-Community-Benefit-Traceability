import unittest
from unittest.mock import Mock, patch

from tests.test_agent import DISCOVERY
from tests.test_providers import _coverage_row
from tests.test_openai_provider import _annotation, _proposed, _response, _tool
from tracegraph.agent import Agent
from tracegraph.experimental_prompt import search
from tracegraph.openai_provider import IncompleteWebSearch, OpenAIClient, OpenAIWebResearcher
from tracegraph.providers import ProviderError


GOOD = "https://source.example/public"
OTHER = "https://source.example/unattributed"
QUOTE = "The operator agreed to publish an annual report."


class SearchFailureRecoveryTests(unittest.TestCase):
    def test_completed_sources_survive_trailing_searching_action(self):
        for requested in (4, 2):
            with self.subTest(requested=requested):
                # Both terminal shapes occurred in the archived failures. The
                # mixed statuses themselves were observed in new H009 and H001 runs.
                tools = [_tool([GOOD]) for _ in range(requested)] + [_tool([OTHER], status="searching")]
                response = _response(_proposed((GOOD, QUOTE), (OTHER, QUOTE)), tools=tools,
                                     annotations=[_annotation(OTHER, "Unattributed result")])
                opener, loader, events = Mock(return_value=response), Mock(return_value=QUOTE), []
                client = OpenAIClient("offline", opener=opener, emit=events.append)
                _, citations = OpenAIWebResearcher(client, source_loader=loader)._search(
                    {"kind": "DISCOVER"}, max_uses=requested)
                self.assertEqual([(r["url"], r["excerpt"]) for r in citations.values()], [(GOOD, QUOTE)])
                loader.assert_called_once()
                opener.assert_called_once()
                self.assertEqual(events[0]["web_search_statuses"], ["completed"] * requested + ["searching"])

    def test_mixed_action_urls_and_source_ownership(self):
        for kind in ("open_page", "find_in_page"):
            tool = {"type": "web_search_call", "status": "completed", "action": {"type": kind, "url": GOOD}}
            opener = Mock(return_value=_response(_proposed((GOOD, QUOTE)), tools=[tool, _tool(status="searching")]))
            _, citations = OpenAIWebResearcher(OpenAIClient("offline", opener=opener),
                                               source_loader=lambda *_a, **_k: QUOTE)._search({}, max_uses=2)
            self.assertEqual(citations["c1"]["url"], GOOD)
        client = OpenAIClient("offline", opener=Mock(return_value=_response(_proposed((OTHER, QUOTE)),
            tools=[_tool([]), _tool([OTHER], status="searching")], annotations=[_annotation(OTHER, "Lead")])))
        with self.assertRaises(IncompleteWebSearch):
            client.search({}, system="Search", max_uses=2)

    def test_mixed_result_still_requires_an_exact_quote(self):
        reply = _response(_proposed((GOOD, QUOTE)), tools=[_tool([GOOD]), _tool(status="searching")])
        client = OpenAIClient("offline", opener=Mock(return_value=reply))
        researcher = OpenAIWebResearcher(client, source_loader=lambda *_a, **_k: "different public text")
        _, citations = researcher._search({})
        self.assertFalse(citations)
        self.assertIn(GOOD, researcher._located_sources)

    def test_incomplete_search_gets_only_the_existing_reserve(self):
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                stub = Mock()
                tail = ([], "", {}) if succeeds else IncompleteWebSearch("incomplete")
                stub.search.side_effect = [IncompleteWebSearch("incomplete"), tail]
                events = []
                researcher = OpenAIWebResearcher(stub, search_strategy=search, emit=events.append)
                if succeeds:
                    self.assertIsNone(researcher(DISCOVERY, None)["draft"])
                else:
                    with self.assertRaises(IncompleteWebSearch):
                        researcher(DISCOVERY, None)
                self.assertEqual([c.kwargs["max_uses"] for c in stub.search.call_args_list], [4, 2])
                self.assertEqual(sum(e.get("type") == "search_retry" for e in events), 1)
        for error in (ProviderError("authentication"), ProviderError("refusal")):
            stub = Mock()
            stub.search.side_effect = error
            with self.assertRaises(ProviderError):
                OpenAIWebResearcher(stub, search_strategy=search)(DISCOVERY, None)
            stub.search.assert_called_once()

    def test_optional_followup_failure_returns_all_five_captured_records_to_the_api(self):
        captures = {f"c{i}": {"url": GOOD, "title": "Public declaration",
                               "excerpt": f"The project published verified record {i}.",
                               "context": f"The project published verified record {i}."} for i in range(1, 6)}
        matched = ["c1", "c2", "c3", "c4"]
        response = {"draft": None, "target_status": "IDENTIFIED", "clarification_question": "",
                    "criteria": [_coverage_row(kind="OUTCOME", promise=(), delivery=(), delivery_gap="No later report")],
                    "verdict": "", "reasoning": "", "findings": [],
                    "relevant_citation_ids": matched, "recovery_citation_ids": [], "followup_query": "A later report"}
        for error in (ProviderError("unavailable"), ValueError("unexpected adapter response")):
            with self.subTest(error=type(error).__name__):
                researcher = OpenAIWebResearcher(Mock(), search_strategy=lambda *_a, **_k: ("", captures))
                store = Mock()
                with patch("tracegraph.discovery._structured", return_value=response), patch.object(
                    researcher, "_search", side_effect=error
                ) as followup:
                    Agent(Mock(), store, researcher).execute(DISCOVERY)
                followup.assert_called_once()
                store.fail.assert_not_called()
                store.finish_without_run.assert_called_once()
                result = store.finish_without_run.call_args.args[1]
                self.assertEqual(result["outcome"], "PARTIAL_RESEARCH")
                self.assertEqual({f["excerpt"] for f in result["facts"]}, {captures[i]["excerpt"] for i in matched})
                self.assertEqual([r["excerpt"] for r in result["unmatched_records"]], [captures["c5"]["excerpt"]])
                store.finish_discovery.assert_not_called()
