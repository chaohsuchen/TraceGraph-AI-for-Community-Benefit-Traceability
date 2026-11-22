import json
import unittest
from unittest.mock import Mock

from tracegraph.openai_provider import OpenAIWebResearcher


class SourcePassagesTest(unittest.TestCase):
    def test_zero_exact_quotes_can_select_a_span_from_its_own_document(self):
        allowed = "https://example.org/report.pdf"
        relevant = "The Borough agreed to fund up to five grants annually."
        page = "Municipal grant terms. " + relevant + " Applications open in May."
        client = Mock()
        client.search.return_value = ([{"url": allowed, "quote": "The borough provides five grants."}],
                                      "", {allowed: "Annual report"})
        def select(**request):
            data = json.loads(request["messages"][0]["content"])
            option = next(row for row in data["passages"] if relevant in row["text"])
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps({
                "passage_ids": [option["id"], "unknown", option["id"]],
            })}]}
        client.create.side_effect = select
        loader = Mock(return_value=page)
        researcher = OpenAIWebResearcher(client, source_loader=loader)
        _, citations = researcher._search({"kind": "DISCOVER", "target": "What grants did the Borough promise?"})
        self.assertEqual(list(citations), ["c1"])
        result = citations["c1"]
        self.assertEqual(result["url"], allowed)
        self.assertEqual(result["excerpt"], page[result["source_start"]:result["source_end"]])
        self.assertIn(relevant, result["excerpt"])
        loader.assert_called_once()
        client.create.assert_called_once()
        client.search.assert_called_once()

    def test_unreadable_leads_remain_uncited_and_untouched_followup_stays_bounded(self):
        urls = {f"https://example.org/{i}": f"Source {i}" for i in range(5)}
        client = Mock()
        client.search.return_value = ([], "", urls)
        loader = Mock(return_value="")
        researcher = OpenAIWebResearcher(client, source_loader=loader)
        for request in ({"kind": "DISCOVER", "target": "Find the public promise."},
                        {"kind": "DISCOVER", "target": "Find the public promise.", "evidence_gap": "payment"}):
            self.assertEqual(researcher._search(request)[1], {})
        self.assertEqual(loader.call_count, 2)
        client.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
