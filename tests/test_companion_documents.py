import json
import re
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from tests.test_providers import _cited_block, _json_message, _message, _search_blocks, _search_result
from tracegraph.companion_documents import CompanionDocumentResearcher
from tracegraph.openai_provider import OpenAIWebResearcher
from tracegraph.providers import ClaudeWebResearcher


class CompanionDocumentTests(unittest.TestCase):
    def test_search_limits_source_ownership_and_offsets_are_enforced_before_reconciliation(self):
        base = "https://agency.example/"
        promise, previous, routine = (base + s for s in ("agreement", "report", "summary"))
        forged, a, b, c, unused = (base + s for s in ("fabricated", "annex", "offline", "audit", "later"))
        statements = [
            "Published on 2026-05-03, the agency recorded three shuttle routes in operation.",
            "Overnight shuttles provided evening service to employees from the North Pier.",
            "The shuttle program authorizes a fourth route when occupancy exceeds the target.",
            "The shuttle board increased the audit frequency to quarterly reporting.",
            "The oversight committee lists the fifth shuttle route as still under consideration.",
        ]
        separate = "The auditor observed the authority operating an additional shuttle route."
        pages = {a: "Appendix to the signed agreement. " + " ".join(statements), c: separate}
        read = Mock(side_effect=lambda url, *, timeout: pages.get(url))
        select, search = Mock(), Mock()
        select._timeout_for.return_value = 14
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        researcher = OpenAIWebResearcher(select, search_client=search, source_loader=read, clock=lambda: now)
        stage = CompanionDocumentResearcher(researcher)
        proposals = [{"url": forged, "quote": separate}, {"url": a, "quote": statements[0]}]
        located = {u: u.rsplit("/", 1)[-1] for u in (promise + "#page=2", previous, routine, a, b, c, unused)}
        search.search.return_value = proposals, "", located

        def extract(**kwargs):
            self.assertNotIn("tools", kwargs)
            data = json.loads(kwargs["messages"][0]["content"])
            self.assertNotIn("private-case-marker", str(data))
            self.assertIn("A shuttle was pledged.", data["shown_findings"])
            windows = data["windows"]
            def row(quote, url):
                key = next(k for k, w in windows.items() if w["url"] == url and quote in w["excerpt"])
                return {"window_id": key, "claim": quote, "quote": quote}
            first = row(statements[0], a)
            wrong_owner = {**first, "quote": separate, "claim": separate}
            return _json_message({"findings": [
                wrong_owner, {**first, "quote": "A fabricated quotation that occurs nowhere."},
                first, first, *(row(q, a) for q in statements[1:]),
            ]})
        select.create.side_effect = extract
        context = {"commitment": {"title": "Shuttle service", "id": "private-case-marker",
                                  "original_promise": {"source": {"url": promise}, "excerpt": "Original terms."}}}
        snapshot = {"findings": [{"claim": "A shuttle was pledged.", "supports": [
            {"source": {"url": previous}, "excerpt": "A funded shuttle route."}]}],
                    "assessment": {"reasoning": "private-case-marker"}}
        result = {"evidence": [{"source": {"url": routine}, "excerpt": "Supplementary report.", "summary": "Terms."}]}
        additions = stage(context, snapshot, result)
        self.assertEqual([call.args[0] for call in read.call_args_list], [a, b, c])
        search.search.assert_called_once()
        self.assertEqual(search.search.call_args.kwargs["max_uses"], 2)
        self.assertNotIn("private-case-marker", str(search.search.call_args))
        self.assertEqual([item["excerpt"] for item in additions], statements[:4])
        self.assertEqual(additions[0]["source"]["published_on"], "2026-05-03")
        self.assertIsNone(additions[1]["source"]["published_on"])
        for item in additions:
            self.assertEqual(item["retrieved_at"], now.isoformat())
            start, end = map(int, re.findall(r"\d+", item["locator"]))
            self.assertEqual(pages[item["source"]["url"]][start:end], item["excerpt"])

    def test_claude_uses_only_its_extra_search_allocation_and_leaves_regular_capture_untouched(self):
        url = "https://agency.example/official-report"
        quote = "The shuttle board published its oversight report for the previous year."
        client = Mock()
        client._timeout_for.return_value = 14
        client.create.side_effect = [
            _message(_search_blocks([_search_result(url, "Oversight Report")]), reason="pause_turn"),
            _message([*_search_blocks([_search_result(url, "Oversight Report")]), _cited_block(url, quote, quote)]),
        ]
        researcher = ClaudeWebResearcher(client, source_loader=Mock(return_value=""))
        researcher._located_sources = {"existing": "Existing"}
        researcher._budget_discovery, researcher._remaining_search_uses = True, 1
        additions = CompanionDocumentResearcher(researcher)({"commitment": {"title": "Shuttle"}}, {"findings": []},
                                                             {"evidence": []})
        self.assertEqual(additions, [])
        self.assertEqual([c.kwargs["tools"][0]["max_uses"] for c in client.create.call_args_list], [2, 1])
        self.assertEqual(researcher._remaining_search_uses, 1)
        self.assertEqual(researcher._located_sources, {"existing": "Existing"})
