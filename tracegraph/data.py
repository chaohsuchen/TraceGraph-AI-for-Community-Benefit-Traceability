"""Local storage adapter and fixture projection for the Data/API slice."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from math import sqrt
from threading import RLock
from uuid import uuid4


COMMITMENT_FIELDS = (
    "title", "description", "original_text", "responsible_entity_raw", "beneficiary",
    "obligation_type", "amount_value", "term_periodicity", "geographic_scope",
    "condition_contingency", "milestone", "notes",
)
SOURCE_FIELDS = ("title", "url", "citation", "publisher", "source_type", "published_on")
EVIDENCE_FIELDS = ("status", "explanation", "excerpt", "summary", "locator", "retrieved_at")


class StaleClaim(RuntimeError):
    """The job is no longer running under the supplied claim."""


class IdempotencyConflict(ValueError):
    """The same key was used for a different request."""


@dataclass
class SearchSnapshot:
    catalog_revision: str
    records: list
    embeddings: dict

    def scan(self):
        return deepcopy(self.records)

    def vector_top_k(self, query_embedding, k):
        def similarity(vector):
            if len(vector) != len(query_embedding):
                raise ValueError("Query and commitment embedding dimensions differ.")
            norm = sqrt(sum(a * a for a in vector) * sum(b * b for b in query_embedding))
            if not norm:
                raise ValueError("Cosine similarity requires nonzero embeddings.")
            return sum(a * b for a, b in zip(vector, query_embedding)) / norm

        ids = sorted(self.embeddings, key=lambda cid: (-similarity(self.embeddings[cid]), cid))
        return [{"commitment_id": cid, "rank": rank}
                for rank, cid in enumerate(ids[:k], start=1)]


class MemoryStore:
    """Store contract fixtures and new research results in one test process."""

    def __init__(self, graphs=(), groups=(), embeddings=None):
        self._lock = RLock()
        self._revision = 0
        self._organizations = {}
        self._commitments = {}
        self._runs = defaultdict(list)
        self._edges = {}
        self._sources = {}
        self._embeddings = deepcopy(embeddings) if embeddings else {}
        self._jobs = {}
        self._claims = {}
        self._responses = {}
        for graph in deepcopy(list(graphs)):
            commitment = graph["commitment"]
            self._save_commitment(commitment, graph["organization"])
            if graph["latest_completed_run"]:
                self._runs[commitment["id"]] = [
                    graph["latest_completed_run"], *graph["earlier_completed_runs"],
                ]
                self._edges[graph["viewed_run_id"]] = graph["source_edges"]
                for edge in graph["source_edges"]:
                    source = edge["source"]
                    self._sources[source["id"]] = source
            original = commitment["original_promise"]
            if original and original["source"]:
                self._sources[original["source"]["id"]] = original["source"]
        for group in deepcopy(list(groups)):
            organization = group["organization"]
            self._organizations[organization["id"]] = organization
            for summary in group["commitments"]:
                if summary["id"] in self._commitments:
                    continue
                commitment = dict.fromkeys(COMMITMENT_FIELDS)
                commitment.update(summary, beneficiary={"category": None, "name": None},
                                  original_promise=None)
                self._save_commitment(commitment, organization)

        if self._embeddings.keys() - self._commitments.keys():
            raise ValueError("Every seed embedding must belong to a seeded commitment.")

    def _save_commitment(self, commitment, organization):
        if organization:
            self._organizations[organization["id"]] = organization
        self._commitments[commitment["id"]] = (
            commitment, organization["id"] if organization else None,
        )

    def with_snapshot(self, read):
        with self._lock:
            records = []
            for cid, (commitment, org_id) in self._commitments.items():
                if cid not in self._embeddings:
                    continue
                record = {field: value for field, value in commitment.items()
                          if field not in ("id", "original_promise", "original_text", "notes")}
                record.update(
                    commitment_id=cid, organization_id=org_id,
                    organization_name=self._organizations[org_id]["name"] if org_id else None,
                )
                records.append(record)
            snapshot = SearchSnapshot(str(self._revision), deepcopy(records), deepcopy(self._embeddings))
        return read(snapshot)

    def load_commitment(self, commitment_id):
        with self._lock:
            record = self._commitments.get(commitment_id)
            if record is None:
                return None
            commitment, org_id = record
            return deepcopy({"commitment": commitment, "organization": self._organizations.get(org_id)})

    def graph(self, commitment_id, run_id=None):
        with self._lock:
            result = self.load_commitment(commitment_id)
            if result is None:
                return None
            runs = self._runs[commitment_id]
            latest = runs[0] if runs else None
            if run_id is None:
                run_id = latest["id"] if latest else None
            elif all(run["id"] != run_id for run in runs):
                return None
            result.update(
                latest_completed_run=latest, earlier_completed_runs=runs[1:], viewed_run_id=run_id,
                source_edges=self._edges.get(run_id, []),
            )
            return deepcopy(result)

    def organization_commitments(self, organization_id):
        with self._lock:
            organization = self._organizations.get(organization_id)
            if organization is None:
                return None
            commitments = [
                {field: item[field] for field in ("id", "title", "description")}
                for item, org_id in self._commitments.values() if org_id == organization_id
            ]
            return deepcopy({"organization": organization,
                             "commitments": sorted(commitments, key=lambda row: row["id"])})

    def submit(self, scope, key, request, action):
        """Replay a successful response or record the first response and job."""
        with self._lock:
            previous = self._responses.get((scope, key))
            if previous is not None:
                prior_request, response = previous
                if prior_request != request:
                    raise IdempotencyConflict
                return deepcopy(response)
            response = action()
            if response[0] in (200, 202):
                self._responses[scope, key] = (deepcopy(request), deepcopy(response))
            return response

    def enqueue(self, kind, **payload):
        with self._lock:
            job_id = f"job-{uuid4()}"
            self._jobs[job_id] = {"job_id": job_id, "status": "QUEUED", "kind": kind, **payload}
            return job_id

    def claim_next(self):
        with self._lock:
            for job in self._jobs.values():
                if job["status"] != "QUEUED":
                    continue
                token = str(uuid4())
                job["status"] = "RUNNING"
                self._claims[job["job_id"]] = token
                return {"claim_token": token, **{
                    field: job[field] for field in ("job_id", "kind", "query", "commitment_id")
                    if field in job
                }}
            return None

    def get_job(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return deepcopy({field: job[field] for field in ("job_id", "status", "result", "error")
                             if field in job})

    def _running_job(self, claim):
        job_id = claim["job_id"]
        job = self._jobs.get(job_id)
        if job is None or job["status"] != "RUNNING" or self._claims[job_id] != claim["claim_token"]:
            raise StaleClaim(job_id)
        return job

    @staticmethod
    def _source(draft, sources):
        source_id = draft.get("source_id")
        if source_id in sources:
            return sources[source_id]
        url = draft.get("url")
        if url:
            for source in sources.values():
                if source["url"] == url:
                    return source
        if not draft.get("title") or not (url or draft.get("citation")):
            raise ValueError("A source requires a title and a URL or citation.")
        source = {field: draft.get(field) for field in SOURCE_FIELDS}
        source["id"] = source_id or f"source-{uuid4()}"
        sources[source["id"]] = source
        return source

    def _prepare_run(self, result, sources):
        fields = ("verification_date", "verdict", "reasoning")
        if any(not result.get(field) for field in fields):
            raise ValueError("A live run requires a verification date, verdict and reasoning.")
        run = {field: result[field] for field in fields}
        run["id"] = f"run-{uuid4()}"
        edges = {}
        for draft in result["evidence"]:
            if not draft.get("retrieved_at"):
                raise ValueError("Evidence from a live run requires a retrieval date.")
            source = self._source(draft["source"], sources)
            edge = edges.setdefault(source["id"], {"source": source, "evidence": []})
            evidence = {field: draft.get(field) for field in EVIDENCE_FIELDS}
            evidence["id"] = f"evidence-{uuid4()}"
            edge["evidence"].append(evidence)
        return run, list(edges.values())

    def _finish(self, job, **terminal):
        job.update(terminal)
        self._claims.pop(job["job_id"])

    def _save_run(self, job, cid, run, edges, sources):
        self._runs[cid].insert(0, run)
        self._edges[run["id"]] = edges
        self._sources = sources
        ids = {"commitment_id": cid, "run_id": run["id"]}
        self._finish(job, status="COMPLETED", result={"outcome": "RESOLVED", **ids})
        return {"outcome": "COMMITTED", **ids}

    def finish_discovery(self, claim, expected_catalog_revision, draft, result):
        with self._lock:
            job = self._running_job(claim)
            if job["kind"] != "DISCOVER":
                raise ValueError("The job does not represent a discovery.")
            if expected_catalog_revision != str(self._revision):
                return {"outcome": "RECHECK_REQUIRED"}
            promise = draft.get("original_promise")
            if not promise or not promise.get("locator") or not promise.get("excerpt"):
                raise ValueError("A new commitment requires a source, locator, and original excerpt.")
            if not draft.get("description") or not draft.get("embedding"):
                raise ValueError("A new commitment requires a description and embedding.")
            embedding = list(draft["embedding"])
            sources = deepcopy(self._sources)
            source = self._source(promise["source"], sources)
            run, edges = self._prepare_run(result, sources)
            org_id = draft.get("organization_id")
            organization = self._organizations.get(org_id)
            if organization is None and draft.get("organization_name"):
                organization = next((
                    item for item in self._organizations.values()
                    if item["name"].casefold() == draft["organization_name"].casefold()
                ), {"id": org_id or f"org-{uuid4()}", "name": draft["organization_name"]})
            cid = f"commitment-{uuid4()}"
            commitment = {field: deepcopy(draft.get(field)) for field in COMMITMENT_FIELDS}
            commitment.update(
                id=cid, beneficiary=commitment["beneficiary"] or {"category": None, "name": None},
                original_promise={
                    "document_id": None, "source": source, "locator": promise["locator"],
                    "char_start": None, "char_end": None,
                },
            )
            commitment["original_text"] = commitment["original_text"] or promise["excerpt"]
            self._save_commitment(commitment, organization)
            self._embeddings[cid] = embedding
            self._revision += 1
            return self._save_run(job, cid, run, edges, sources)

    def finish_refresh(self, claim, result):
        with self._lock:
            job = self._running_job(claim)
            if job["kind"] != "REFRESH":
                raise ValueError("The job does not represent a refresh.")
            sources = deepcopy(self._sources)
            run, edges = self._prepare_run(result, sources)
            return self._save_run(job, job["commitment_id"], run, edges, sources)

    def finish_without_run(self, claim, outcome):
        with self._lock:
            job = self._running_job(claim)
            if job["kind"] != "DISCOVER":
                raise ValueError("A refresh cannot complete without a run.")
            self._finish(job, status="COMPLETED", result=deepcopy(outcome))

    def fail(self, claim, error):
        with self._lock:
            self._finish(self._running_job(claim), status="FAILED", error=deepcopy(error))
