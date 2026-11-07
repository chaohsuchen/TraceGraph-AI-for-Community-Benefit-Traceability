import unittest
from unittest.mock import Mock

from tracegraph.retrieval import (
    CatalogError,
    EmbeddingError,
    InvalidJudgeResponse,
    InvalidResolutionInput,
    JudgeError,
    Retrieval,
)

H001 = {
    "commitment_id": "TG-GOLD-H001",
    "description": "Bally's commits to a diverse 3,503-FTE permanent workforce, 70 percent from the Bronx.",
    "organization_name": "Bally’s Corporation",
    "term_periodicity": "permanent operations",
    "geographic_scope": "Bronx",
}
H008 = {
    "commitment_id": "TG-GOLD-H008",
    "description": "Bally's committed up to $10 million for a new police substation and $5 million per year in public safety support.",
    "organization_name": "Bally’s Corporation",
    "beneficiary": {"category": "public safety", "name": "new police substation"},
    "geographic_scope": "Bronx",
    "embedding": [0.1, 0.2],
    "evidence": [{"source_id": "must-not-leave-catalog"}],
}
H009 = {
    "commitment_id": "TG-GOLD-H009",
    "description": "Bally's committed that 70 percent of construction and facility jobs go to Bronx residents.",
    "organization_name": "Bally’s Corporation",
    "term_periodicity": "construction and facility jobs",
    "geographic_scope": "Bronx",
}


class Snapshot:
    catalog_revision = "catalog-3"

    def __init__(self, records, vector_ids):
        self.records = records
        self.vector_ids = vector_ids
        self.active = False
        self.vector_requests = []

    def scan(self):
        assert self.active
        return self.records

    def vector_top_k(self, embedding, k):
        assert self.active
        self.vector_requests.append((embedding, k))
        return [
            {"commitment_id": commitment_id, "rank": rank}
            for rank, commitment_id in enumerate(self.vector_ids[:k], 1)
        ]


class Catalog:
    def __init__(self, records=(), vector_ids=()):
        self.snapshot = Snapshot(records, vector_ids)

    def with_snapshot(self, read):
        self.snapshot.active = True
        try:
            return read(self.snapshot)
        finally:
            self.snapshot.active = False


class RetrievalTests(unittest.TestCase):
    def test_h008_paraphrase_ranks_first_with_both_lists(self):
        catalog = Catalog([H001, H008, H009], ["TG-GOLD-H001", "TG-GOLD-H008"])
        embed = Mock(return_value=[1, 0])
        query = "What did Bally’s promise to fund for a Bronx police substation and annual public safety?"

        def judge(request, candidates):
            self.assertFalse(catalog.snapshot.active)
            self.assertEqual(request, {"query": query})
            self.assertEqual(candidates[0]["commitment_id"], "TG-GOLD-H008")
            self.assertEqual(candidates[0]["beneficiary"]["category"], "public safety")
            self.assertIsNone(candidates[0]["milestone"])
            self.assertNotIn("embedding", candidates[0])
            self.assertNotIn("evidence", candidates[0])
            return {"outcome": "MATCH", "commitment_id": candidates[0]["commitment_id"]}

        retrieval = Retrieval(catalog, embed, judge)
        self.assertEqual(
            retrieval.resolve({"query": f" {query} "}),
            {"outcome": "MATCH", "commitment_id": "TG-GOLD-H008"},
        )
        embed.assert_called_once_with(query)
        self.assertEqual(catalog.snapshot.vector_requests, [([1.0, 0.0], 20)])

    def test_distinguishes_permanent_operations_and_construction_for_clarification(self):
        catalog = Catalog([H008, H001, H009], ["TG-GOLD-H009", "TG-GOLD-H001"])
        question = "Do you mean permanent operations, or construction and facility jobs?"

        def judge(request, candidates):
            self.assertEqual(
                {candidate["term_periodicity"] for candidate in candidates},
                {"permanent operations", "construction and facility jobs"},
            )
            return {"outcome": "AMBIGUOUS", "question": question}

        retrieval = Retrieval(catalog, lambda _: [1], judge, top_k=2)
        self.assertEqual(
            retrieval.resolve({"query": "What was Bally’s 70% Bronx hiring promise?"}),
            {"outcome": "AMBIGUOUS", "question": question},
        )

    def test_rrf_uses_ranks_and_preserves_single_retriever_candidates(self):
        records = [
            {"commitment_id": "lexical", "description": "community shuttle"},
            {"commitment_id": "shared", "description": "regional commuter shuttle"},
            {"commitment_id": "vector", "description": "commuter transport"},
        ]
        catalog = Catalog(records, ["vector", "shared"])
        judge = Mock(return_value={"outcome": "NO_MATCH"})
        retrieval = Retrieval(catalog, lambda _: [1], judge, top_k=3, rank_k=3)

        retrieval.resolve({"query": "community shuttle"})

        candidates = judge.call_args[0][1]
        self.assertEqual(
            [candidate["commitment_id"] for candidate in candidates],
            ["shared", "lexical", "vector"],
        )

    def test_discovered_identity_uses_description_for_embedding_and_snapshot_revision(self):
        catalog = Catalog([H001])
        embed = Mock(return_value=(1.0, 0.0))
        judge = Mock(return_value={"outcome": "NO_MATCH", "catalog_revision": "wrong"})
        discovered = {
            "description": "Documented employment pledge.",
            "organization_name": "Ballys Corporation",
            "geographic_scope": "Bronx",
            "original_promise": {"excerpt": "The originating pledge"},
        }
        retrieval = Retrieval(catalog, embed, judge)
        self.assertEqual(
            retrieval.resolve({"discovered": discovered}),
            {"outcome": "NO_MATCH", "catalog_revision": "catalog-3"},
        )
        embed.assert_called_once_with(discovered["description"])
        self.assertEqual(judge.call_args[0][0], {"discovered": discovered})
        self.assertEqual(judge.call_args[0][1][0]["commitment_id"], "TG-GOLD-H001")
        self.assertEqual(retrieval.embed_commitment(" A promise "), [1.0, 0.0])

    def test_rejects_match_id_present_in_catalog_but_outside_top_k(self):
        catalog = Catalog([H001, H008], ["TG-GOLD-H008"])
        judge = Mock(return_value={"outcome": "MATCH", "commitment_id": "TG-GOLD-H001"})
        retrieval = Retrieval(catalog, lambda _: [1], judge, top_k=1, rank_k=1)
        with self.assertRaises(InvalidJudgeResponse):
            retrieval.resolve({"query": "NYPD public safety substation"})

    def test_malformed_model_answers_are_errors(self):
        for answer in (None, {}, {"outcome": "RESEARCH"}, {"outcome": "AMBIGUOUS", "question": " "}):
            with self.subTest(answer=answer):
                retrieval = Retrieval(Catalog(), lambda _: [1], Mock(return_value=answer))
                with self.assertRaises(InvalidJudgeResponse):
                    retrieval.resolve({"query": "What did the organization promise?"})

    def test_model_outage_raises_judge_error(self):
        error = TimeoutError("judge timed out")
        retrieval = Retrieval(Catalog(), lambda _: [1], Mock(side_effect=error))
        with self.assertRaises(JudgeError) as raised:
            retrieval.resolve({"query": "a promise"})
        self.assertIs(raised.exception.__cause__, error)

    def test_rejects_unknown_vector_ids_and_does_not_call_judge(self):
        catalog = Catalog([H008], ["missing-id"])
        judge = Mock()
        retrieval = Retrieval(catalog, lambda _: [1], judge)
        with self.assertRaises(CatalogError):
            retrieval.resolve({"query": "a promise"})
        judge.assert_not_called()

    def test_embedding_must_be_finite_and_nonempty(self):
        for vector in ([], [float("nan")], [float("inf")], [True], ["1"]):
            with self.subTest(vector=vector):
                retrieval = Retrieval(Catalog(), Mock(return_value=vector), Mock())
                with self.assertRaises(EmbeddingError):
                    retrieval.embed_commitment("a promise")

    def test_blank_or_mixed_resolution_input_is_rejected_before_provider_call(self):
        embed = Mock()
        retrieval = Retrieval(Catalog(), embed, Mock())
        for value in ({"query": " "}, {"discovered": {}}, {"query": "a", "discovered": {}}):
            with self.subTest(value=value):
                with self.assertRaises(InvalidResolutionInput):
                    retrieval.resolve(value)
        embed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
