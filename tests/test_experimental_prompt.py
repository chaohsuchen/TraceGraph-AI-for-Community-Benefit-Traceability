import json
import unittest
from unittest.mock import Mock, patch

from tracegraph.experimental_prompt import evaluate_capture, search
from tracegraph.providers import ClaudeWebResearcher, ProviderError
from tracegraph.runtime import create_application

_REQUEST = {"kind": "DISCOVER", "target": "When did the Transit Board promise to open the Harbor Line?"}

# Synthetic H011-style evidence. The later timetable changes the plan but does
# not establish when the line opened or alter the original comparison date.
_ORIGINAL_URL = "https://transit.example/board/approval"
_ORIGINAL_TITLE = "Transit Board approval"
_ORIGINAL_EXCERPT = "The Transit Board commits to opening the Harbor Line by June 2024."
_ORIGINAL_CLAIM = "Original promise and timetable baseline: open the Harbor Line by June 2024."
_LATER_URL = "https://transit.example/board/project-update"
_LATER_TITLE = "Transit Board project update"
_LATER_EXCERPT = "The Transit Board now expects the Harbor Line to open by December 2025."
_LATER_CLAIM = "Later plan: the Board expected an opening by December 2025."
_ASSESSMENT = "Opening remains unverified. A service commencement record could resolve the gap."


def _message(*blocks, reason="end_turn"):
    return {"type": "message", "content": list(blocks), "stop_reason": reason}


def _results(*sources):
    return {
        "type": "web_search_tool_result",
        "content": [{"type": "web_search_result", "url": url, "title": title,
                     "encrypted_content": "opaque-payload"} for url, title in sources],
    }


def _text(text, *, url=None, excerpt=None):
    block = {"type": "text", "text": text}
    if url is not None:
        block["citations"] = [{"type": "web_search_result_location", "url": url,
                               "encrypted_index": "opaque-index", "cited_text": excerpt}]
    return block


def _h011_message():
    return _message(
        _results((_ORIGINAL_URL, _ORIGINAL_TITLE), (_LATER_URL, _LATER_TITLE)),
        _text(_ORIGINAL_CLAIM, url=_ORIGINAL_URL, excerpt=_ORIGINAL_EXCERPT),
        _text(_LATER_CLAIM, url=_LATER_URL, excerpt=_LATER_EXCERPT),
        _text(_ASSESSMENT),
        _text("STOP_REASON=BUDGET"),
    )


class PromptExperimentTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.researcher = ClaudeWebResearcher(self.client)
        self.events = []

    def test_live_runtime_defaults_to_prompt_discovery_and_retains_control(self):
        with (
            patch("tracegraph.runtime.check_embedding_profile"),
            patch("tracegraph.runtime.read_credential", return_value="fixture"),
            patch("tracegraph.providers.ClaudeWebResearcher") as researcher,
        ):
            create_application("fixture-database", "live")
            self.assertIs(researcher.call_args.kwargs["search_strategy"], search)

            create_application("fixture-database", "live", search_strategy=None)
            self.assertIsNone(researcher.call_args.kwargs["search_strategy"])

    def test_one_hosted_search_turn_with_six_uses(self):
        self.client.create.return_value = _h011_message()

        search(self.researcher, {**_REQUEST, "internal": "private job data"}, emit=self.events.append)

        self.client.create.assert_called_once()
        call = self.client.create.call_args.kwargs
        self.assertEqual(set(call), {"system", "messages", "max_tokens", "tools"})
        self.assertEqual(call["max_tokens"], 4096)
        self.assertEqual(call["tools"], [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}])
        self.assertEqual(json.loads(call["messages"][0]["content"]), _REQUEST)
        self.assertIn("search again only for a concrete gap likely to", call["system"])
        self.assertEqual(self.events, [
            {"phase": "search", "event": "started"},
            {"phase": "search", "event": "completed", "source": "self_reported",
             "stop_reason": "BUDGET", "stop_reason_status": "reported"},
        ])

    def test_pauses_are_continued_by_the_existing_adapter(self):
        initial = _message(_results((_ORIGINAL_URL, _ORIGINAL_TITLE)), reason="pause_turn")
        follow_up = _message(_results((_LATER_URL, _LATER_TITLE)), reason="pause_turn")
        final = _h011_message()
        final["content"] = final["content"][1:]
        self.client.create.side_effect = [initial, follow_up, final]

        with patch.object(self.researcher, "_search", wraps=self.researcher._search) as adapter:
            _, citations = search(self.researcher, _REQUEST, emit=self.events.append)

        adapter.assert_called_once()
        self.assertEqual(self.client.create.call_count, 3)
        first, second, third = [call.kwargs for call in self.client.create.call_args_list]
        initial_message = {"role": "assistant", "content": initial["content"]}
        follow_up_message = {"role": "assistant", "content": follow_up["content"]}
        self.assertEqual(second["messages"], [*first["messages"], initial_message])
        self.assertEqual(third["messages"], [*second["messages"], follow_up_message])
        for continued in (second, third):
            self.assertEqual(continued["tools"], first["tools"])
            self.assertEqual(continued["max_tokens"], 4096)
            self.assertEqual(continued["system"], first["system"])
        self.assertEqual([item["url"] for item in citations.values()], [_ORIGINAL_URL, _LATER_URL])

    def test_continuation_exhaustion_propagates_without_a_new_turn(self):
        self.client.create.return_value = _message(_results(), reason="pause_turn")

        with self.assertRaisesRegex(ProviderError, "continuation limit"):
            search(self.researcher, _REQUEST, emit=self.events.append)

        self.assertEqual(self.client.create.call_count, 3)
        self.assertEqual(self.events, [{"phase": "search", "event": "started"}])

    def test_missing_original_keeps_the_outcome_uncertain(self):
        self.client.create.return_value = _message(
            _results((_LATER_URL, _LATER_TITLE)),
            _text(_LATER_CLAIM, url=_LATER_URL, excerpt=_LATER_EXCERPT),
            _text("No excerpt establishes the original promise. The obligation and outcome remain uncertain."),
            _text("STOP_REASON=NO_ACTIONABLE_LEAD"),
        )

        brief, citations = search(self.researcher, _REQUEST, emit=self.events.append)

        self.assertIn("No excerpt establishes the original promise", brief)
        self.assertIn("outcome remain uncertain", brief)
        self.assertEqual(list(citations), ["c1"])
        self.assertEqual(citations["c1"]["excerpt"], _LATER_EXCERPT)
        self.assertEqual(self.events[-1]["stop_reason"], "NO_ACTIONABLE_LEAD")
        self.assertIn("keep the obligation and outcome\nuncertain", self.client.create.call_args.kwargs["system"])

    def test_h011_old_timetable_stays_the_baseline_in_search_and_replay(self):
        self.client.create.return_value = _h011_message()
        brief, citations = search(self.researcher, _REQUEST, emit=self.events.append)

        self.assertEqual(list(citations), ["c1", "c2"])
        self.assertEqual(citations["c1"], {
            "url": _ORIGINAL_URL, "title": _ORIGINAL_TITLE, "excerpt": _ORIGINAL_EXCERPT, "context": _ORIGINAL_CLAIM,
        })
        self.assertEqual(citations["c2"], {
            "url": _LATER_URL, "title": _LATER_TITLE, "excerpt": _LATER_EXCERPT, "context": _LATER_CLAIM,
        })
        self.assertEqual(brief.splitlines(), [_ORIGINAL_CLAIM, _LATER_CLAIM, _ASSESSMENT, "STOP_REASON=BUDGET"])
        search_system = self.client.create.call_args.kwargs["system"]
        self.client.reset_mock()
        replay_text = f"{_ORIGINAL_CLAIM} [c1]\n{_LATER_CLAIM} [c2]\n{_ASSESSMENT}\nSTOP_REASON=BUDGET"
        self.client.create.return_value = _message(_text(json.dumps({"brief": replay_text})))

        with patch.object(self.researcher, "_search") as adapter:
            replay, returned = evaluate_capture(self.researcher, _REQUEST, brief, citations, emit=self.events.append)

        adapter.assert_not_called()
        self.client.create.assert_called_once()
        self.assertEqual(replay, replay_text)
        self.assertIs(returned, citations)
        call = self.client.create.call_args.kwargs
        self.assertEqual(set(call), {"system", "messages", "max_tokens", "output_config"})
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        payload = json.loads(call["messages"][0]["content"])
        self.assertEqual(payload, {**_REQUEST, "citations": {
            "c1": {"url": _ORIGINAL_URL, "title": _ORIGINAL_TITLE, "excerpt": _ORIGINAL_EXCERPT},
            "c2": {"url": _LATER_URL, "title": _LATER_TITLE, "excerpt": _LATER_EXCERPT},
        }})
        for system in (search_system, call["system"]):
            self.assertIn("Use the original timetable as the baseline.", system)
            self.assertIn("Record later plans and revised dates\nseparately.", system)
        self.assertEqual(self.events[-1], {
            "phase": "replay", "event": "completed", "source": "self_reported",
            "stop_reason": "BUDGET", "stop_reason_status": "reported",
        })

    def test_missing_or_malformed_stop_reason_is_explicit(self):
        for suffix, status in (
            ("", "missing"),
            ("\nSTOP_REASON=UNKNOWN", "malformed"),
            ("\nSTOP REASON=BUDGET", "malformed"),
            ("\nSTOP_REASON=SUFFICIENT\nTrailing text", "malformed"),
            ("\nSTOP_REASON=STALLED\nSTOP_REASON=BUDGET", "malformed"),
        ):
            with self.subTest(suffix=suffix):
                brief = "The original promise remains uncertain." + suffix
                self.client.create.return_value = _message(_results(), _text(brief))

                returned, _ = search(self.researcher, _REQUEST, emit=self.events.append)

                self.assertEqual(returned, brief)
                self.assertEqual(self.events[-1], {
                    "phase": "search", "event": "completed", "source": "self_reported",
                    "stop_reason": None, "stop_reason_status": status,
                })

    def test_other_job_kinds_are_rejected_before_calling_the_provider(self):
        refresh = {**_REQUEST, "kind": "REFRESH"}
        with self.assertRaisesRegex(ProviderError, "requires DISCOVER"):
            search(self.researcher, refresh, emit=self.events.append)
        with self.assertRaisesRegex(ProviderError, "requires DISCOVER"):
            evaluate_capture(self.researcher, refresh, "", {}, emit=self.events.append)
        self.client.create.assert_not_called()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
