import unittest

from tracegraph.research_experiment_score import estimate_usage


class ExperimentUsageTests(unittest.TestCase):
    def test_charges_tokens_caching_and_searches_separately(self):
        usage = {
            "input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 2000,
            "cache_creation_input_tokens": 3000,
            "cache_creation": {"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 1000},
            "server_tool_use": {"web_search_requests": 2},
        }
        result = estimate_usage([{"type": "provider_call", "usage": usage, "web_search_ids": ["s1", "s2"]}])
        self.assertEqual(result["estimated_usd"], 0.0401)
        self.assertTrue(result["complete"])
        self.assertEqual(result["counts"]["observed_search_ids"], 2)

    def test_keeps_reported_cost_and_flags_unknown_usage(self):
        events = [
            {"type": "provider_call", "usage": None, "web_search_ids": ["s1"]},
            {"type": "provider_call", "usage": {"input_tokens": 1000, "output_tokens": 0},
             "web_search_ids": ["s1"]},
        ]
        result = estimate_usage(events)
        self.assertEqual(result["estimated_usd"], 0.003)
        self.assertFalse(result["complete"])
        self.assertEqual(result["counts"]["calls_missing_usage"], 1)
        self.assertEqual(result["counts"]["observed_search_ids"], 1)
