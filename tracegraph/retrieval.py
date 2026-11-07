"""Hybrid commitment retrieval and provider-independent identity resolution."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from numbers import Real

_CANDIDATE_FIELDS = (
    "commitment_id", "title", "description", "organization_id", "organization_name",
    "responsible_entity_raw", "beneficiary", "obligation_type", "amount_value",
    "term_periodicity", "geographic_scope", "condition_contingency", "milestone",
)
_SEARCH_FIELDS = tuple(
    field for field in _CANDIDATE_FIELDS if field not in {"commitment_id", "organization_id"}
)
_WORD = re.compile(r"[^\W_]+")
_APOSTROPHES = str.maketrans("", "", "'’‘")


class ResolutionError(RuntimeError):
    """A failed lookup that cannot be treated as NO_MATCH."""


class CatalogError(ResolutionError):
    """A catalog read failed or violated the snapshot contract."""


class EmbeddingError(ResolutionError):
    """The embedding provider failed or returned an invalid vector."""


class JudgeError(ResolutionError):
    """The identity judge failed."""


class InvalidJudgeResponse(JudgeError):
    """The identity judge returned an invalid outcome, ID, or question."""


class InvalidResolutionInput(ValueError):
    """The caller did not supply a query or discovered commitment identity."""


class Retrieval:
    """Rank one catalog snapshot, then pass compact candidates to the judge.

    The catalog exposes with_snapshot(callback). The injected functions are
    embed(text) and judge(input_dict, candidates). The judge is called after
    the snapshot callback returns so a model call does not hold a database read.
    """

    def __init__(self, catalog, embed, judge, *, top_k=5, rank_k=20, rrf_k=60):
        if any(type(value) is not int or value < 1 for value in (top_k, rank_k, rrf_k)):
            raise ValueError("top_k, rank_k, and rrf_k must be positive integers")
        if rank_k < top_k:
            raise ValueError("rank_k must be at least top_k")
        self._catalog = catalog
        self._embed = embed
        self._judge = judge
        self._top_k = top_k
        self._rank_k = rank_k
        self._rrf_k = rrf_k

    def embed_commitment(self, description: str) -> list[float]:
        if not isinstance(description, str) or not description.strip():
            raise InvalidResolutionInput("A nonempty description is required")
        try:
            vector = self._embed(description.strip())
            if not isinstance(vector, Sequence) or not vector:
                raise EmbeddingError("Expected a nonempty numeric embedding")
            if any(isinstance(value, bool) or not isinstance(value, Real) for value in vector):
                raise EmbeddingError("Expected a numeric embedding")
            result = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in result):
                raise EmbeddingError("Embedding values must be finite")
            return result
        except EmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingError("Embedding provider failed") from error

    def resolve(self, input_dict: dict) -> dict:
        judge_input, vector_text, lexical_text = _prepare_input(input_dict)
        embedding = self.embed_commitment(vector_text)
        try:
            revision, candidates = self._catalog.with_snapshot(
                lambda snapshot: self._read_candidates(snapshot, embedding, lexical_text)
            )
        except CatalogError:
            raise
        except Exception as error:
            raise CatalogError("Catalog snapshot read failed") from error

        candidate_ids = {candidate["commitment_id"] for candidate in candidates}
        try:
            answer = self._judge(judge_input, candidates)
        except JudgeError:
            raise
        except Exception as error:
            raise JudgeError("Identity judge failed") from error
        return _validate_answer(answer, candidate_ids, revision)

    def _read_candidates(self, snapshot, embedding, lexical_text):
        revision = snapshot.catalog_revision
        if not isinstance(revision, str) or not revision.strip():
            raise CatalogError("Catalog snapshot has no revision")

        records = {}
        for record in snapshot.scan():
            commitment_id = record["commitment_id"]
            if not isinstance(commitment_id, str) or not commitment_id.strip():
                raise CatalogError("Catalog scan returned an invalid commitment ID")
            if commitment_id in records:
                raise CatalogError("Catalog scan returned a duplicate commitment ID")
            records[commitment_id] = {field: record.get(field) for field in _CANDIDATE_FIELDS}

        vector_ranks = {}
        for item in snapshot.vector_top_k(embedding, self._rank_k):
            commitment_id, rank = item["commitment_id"], item["rank"]
            if commitment_id not in records or commitment_id in vector_ranks:
                raise CatalogError("Vector ranking returned an unknown or duplicate commitment ID")
            if type(rank) is not int or not 1 <= rank <= self._rank_k:
                raise CatalogError("Vector ranking returned an invalid rank")
            vector_ranks[commitment_id] = rank
        if set(vector_ranks.values()) != set(range(1, len(vector_ranks) + 1)):
            raise CatalogError("Vector ranking must use unique consecutive ranks")

        lexical_ranks = _bm25_rank(records, lexical_text, self._rank_k)
        scores = {}
        for ranking in (lexical_ranks, vector_ranks):
            for commitment_id, rank in ranking.items():
                scores[commitment_id] = scores.get(commitment_id, 0.0) + 1 / (self._rrf_k + rank)
        ranked_ids = sorted(scores, key=lambda commitment_id: (-scores[commitment_id], commitment_id))
        return revision, [records[commitment_id] for commitment_id in ranked_ids[:self._top_k]]


def _prepare_input(input_dict):
    if not isinstance(input_dict, Mapping):
        raise InvalidResolutionInput("Expected a query or discovered identity object")
    if input_dict.keys() == {"query"}:
        query = input_dict["query"]
        if isinstance(query, str) and query.strip():
            query = query.strip()
            return {"query": query}, query, query
    if input_dict.keys() == {"discovered"}:
        discovered = input_dict["discovered"]
        if isinstance(discovered, Mapping):
            description = discovered.get("description")
            if isinstance(description, str) and description.strip():
                return {"discovered": dict(discovered)}, description, _identity_text(discovered)
    raise InvalidResolutionInput("Expected a nonempty query or discovered description")


def _identity_text(record):
    words = []
    for field in _SEARCH_FIELDS:
        value = record.get(field)
        if field == "beneficiary" and isinstance(value, Mapping):
            words.extend(str(value[key]) for key in ("category", "name") if value.get(key) is not None)
        elif value is not None:
            words.append(str(value))
    return " ".join(words)


def _tokens(text):
    normalized = unicodedata.normalize("NFKC", text).casefold().translate(_APOSTROPHES)
    return _WORD.findall(normalized)


def _bm25_rank(records, query, k):
    query_terms = tuple(dict.fromkeys(_tokens(query)))
    if not records or not query_terms:
        return {}
    documents = {
        commitment_id: Counter(_tokens(_identity_text(record)))
        for commitment_id, record in records.items()
    }
    lengths = {commitment_id: sum(terms.values()) for commitment_id, terms in documents.items()}
    average_length = sum(lengths.values()) / len(lengths)
    if not average_length:
        return {}

    document_frequency = Counter()
    for terms in documents.values():
        document_frequency.update(terms.keys())
    inverse_frequency = {
        term: math.log1p((len(documents) - document_frequency[term] + 0.5) /
                         (document_frequency[term] + 0.5))
        for term in query_terms if document_frequency[term]
    }
    k1, b = 1.2, 0.75
    scores = {}
    for commitment_id, terms in documents.items():
        length_normalization = k1 * (1 - b + b * lengths[commitment_id] / average_length)
        score = sum(
            idf * terms[term] * (k1 + 1) / (terms[term] + length_normalization)
            for term, idf in inverse_frequency.items() if term in terms
        )
        if score > 0:
            scores[commitment_id] = score
    ranked_ids = sorted(scores, key=lambda commitment_id: (-scores[commitment_id], commitment_id))
    return {commitment_id: rank for rank, commitment_id in enumerate(ranked_ids[:k], 1)}


def _validate_answer(answer, candidate_ids, revision):
    if not isinstance(answer, Mapping):
        raise InvalidJudgeResponse("The judge response must be an object")
    outcome = answer.get("outcome")
    if outcome == "MATCH":
        commitment_id = answer.get("commitment_id")
        if not isinstance(commitment_id, str) or commitment_id not in candidate_ids:
            raise InvalidJudgeResponse("The judge matched an ID outside the candidate list")
        return {"outcome": "MATCH", "commitment_id": commitment_id}
    if outcome == "AMBIGUOUS":
        question = answer.get("question")
        if not isinstance(question, str) or not question.strip():
            raise InvalidJudgeResponse("An ambiguous result requires a clarification question")
        return {"outcome": "AMBIGUOUS", "question": question.strip()}
    if outcome == "NO_MATCH":
        return {"outcome": "NO_MATCH", "catalog_revision": revision}
    raise InvalidJudgeResponse("The judge returned an unknown outcome")
