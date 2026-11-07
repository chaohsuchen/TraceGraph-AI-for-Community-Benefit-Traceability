"""Durable PostgreSQL and pgvector implementation of TraceGraph's store ports."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from json import dumps
from logging import getLogger
from math import isfinite
from threading import Event, Thread
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tracegraph.data import COMMITMENT_FIELDS, EVIDENCE_FIELDS, SOURCE_FIELDS, IdempotencyConflict, StaleClaim


LOG = getLogger(__name__)

SEARCH_COLUMNS = """
    c.id AS commitment_id, c.title, c.description, c.organization_id, o.name AS organization_name,
    c.responsible_entity_raw,
    jsonb_build_object('category', c.beneficiary_category, 'name', c.beneficiary_named) AS beneficiary,
    c.obligation_type, c.amount_value, c.term_periodicity, c.geographic_scope,
    c.condition_contingency, c.milestone
"""


def _vector(values, dimensions=None):
    values = [float(value) for value in values]
    if dimensions is not None and len(values) != dimensions:
        raise ValueError("Embedding does not match the catalog dimensions.")
    if not values or not any(values) or not all(map(isfinite, values)):
        raise ValueError("Embedding must be a finite nonzero vector.")
    return dumps(values, separators=(",", ":"))


def _new_id(kind):
    return f"{kind}-{uuid4()}"


@dataclass
class _SearchSnapshot:
    catalog_revision: str
    _connection: psycopg.Connection
    _dimensions: int | None

    def scan(self):
        return self._connection.execute(f"""
            SELECT {SEARCH_COLUMNS}
            FROM commitment c
            LEFT JOIN organization o ON o.id = c.organization_id
            ORDER BY c.id
        """).fetchall()

    def vector_top_k(self, query_embedding, k):
        if self._dimensions is None:
            return []
        embedding = _vector(query_embedding, self._dimensions)
        matches = self._connection.execute("""
            SELECT id AS commitment_id
            FROM commitment
            ORDER BY embedding <=> %s::vector, id
            LIMIT %s
        """, (embedding, k)).fetchall()
        return [dict(match, rank=rank) for rank, match in enumerate(matches, start=1)]


class PostgresStore:
    """Read graph and search snapshots and atomically persist research in PostgreSQL."""

    def __init__(self, dsn, lease_seconds=300):
        if not isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Research job lease duration must be positive and finite.")
        self._dsn = dsn
        self._lease_seconds = lease_seconds
        self._submission_connection = ContextVar("tracegraph_submission_connection", default=None)

    def _connect(self):
        return psycopg.connect(self._dsn, row_factory=dict_row)

    @contextmanager
    def _read_snapshot(self):
        with self._connect() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            yield connection

    def with_snapshot(self, read):
        with self._read_snapshot() as connection:
            catalog = connection.execute("""
                SELECT revision, embedding_dimensions FROM tracegraph_catalog WHERE singleton
            """).fetchone()
            return read(_SearchSnapshot(str(catalog["revision"]), connection, catalog["embedding_dimensions"]))

    @staticmethod
    def _load_commitment(connection, commitment_id):
        row = connection.execute(f"""
            SELECT {SEARCH_COLUMNS}, c.original_text, c.notes,
                   c.original_document_id, c.original_locator, c.original_char_start,
                   c.original_char_end, to_jsonb(s) AS original_source
            FROM commitment c
            LEFT JOIN organization o ON o.id = c.organization_id
            LEFT JOIN source s ON s.id = c.original_source_id
            WHERE c.id = %s
        """, (commitment_id,)).fetchone()
        if row is None:
            return None

        original_promise = {
            "document_id": row["original_document_id"], "source": row["original_source"],
            "locator": row["original_locator"], "char_start": row["original_char_start"],
            "char_end": row["original_char_end"],
        }
        commitment = {field: row[field] for field in COMMITMENT_FIELDS}
        commitment.update(
            id=row["commitment_id"],
            original_promise=original_promise if any(value is not None for value in original_promise.values()) else None,
        )
        organization = None
        if row["organization_id"] is not None:
            organization = {"id": row["organization_id"], "name": row["organization_name"]}
        return {"commitment": commitment, "organization": organization}

    def load_commitment(self, commitment_id):
        with self._read_snapshot() as connection:
            return self._load_commitment(connection, commitment_id)

    def graph(self, commitment_id, run_id=None):
        with self._read_snapshot() as connection:
            result = self._load_commitment(connection, commitment_id)
            if result is None:
                return None
            runs = connection.execute("""
                SELECT id, to_jsonb(verification_date) AS verification_date, verdict, reasoning
                FROM research_run
                WHERE commitment_id = %s
                ORDER BY completed_at DESC NULLS LAST, id DESC
            """, (commitment_id,)).fetchall()
            latest = runs[0] if runs else None
            if run_id is None:
                run_id = latest["id"] if latest else None
            elif all(run["id"] != run_id for run in runs):
                return None
            result.update(
                latest_completed_run=latest,
                earlier_completed_runs=runs[1:],
                viewed_run_id=run_id,
                source_edges=self._source_edges(connection, run_id) if run_id else [],
            )
            return result

    @staticmethod
    def _source_edges(connection, run_id):
        return connection.execute("""
            SELECT to_jsonb(s) AS source,
                   jsonb_agg(to_jsonb(e) - 'source_id' - 'run_id' ORDER BY e.id) AS evidence
            FROM evidence e
            JOIN source s ON s.id = e.source_id
            WHERE e.run_id = %s
            GROUP BY s.id
            ORDER BY s.id
        """, (run_id,)).fetchall()

    def organization_commitments(self, organization_id):
        with self._read_snapshot() as connection:
            organization = connection.execute("""
                SELECT id, name FROM organization WHERE id = %s
            """, (organization_id,)).fetchone()
            if organization is None:
                return None
            commitments = connection.execute("""
                SELECT id, title, description FROM commitment WHERE organization_id = %s ORDER BY id
            """, (organization_id,)).fetchall()
            return {"organization": organization, "commitments": commitments}

    def submit(self, scope, key, request, action):
        """Serialize competing submissions for a scope/key and commit a job with its response."""
        scope = dumps(scope, separators=(",", ":"))
        lock_id = "tracegraph:idempotency:" + dumps([scope, key], separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_id,))
            prior = connection.execute("""
                SELECT request, response_status, response_headers, response_body
                FROM api_idempotency
                WHERE scope = %s AND key = %s
            """, (scope, key)).fetchone()
            if prior is not None:
                if prior["request"] != request:
                    raise IdempotencyConflict
                return prior["response_status"], prior["response_headers"], prior["response_body"]

            token = self._submission_connection.set(connection)
            try:
                status, headers, body = action()
            finally:
                self._submission_connection.reset(token)
            if status in (200, 202):
                connection.execute("""
                    INSERT INTO api_idempotency (
                        scope, key, request, response_status, response_headers, response_body, job_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (scope, key, Jsonb(request), status, Jsonb(headers), Jsonb(body),
                      body["job_id"] if status == 202 else None))
            else:
                connection.rollback()
            return status, headers, body

    def enqueue(self, kind, *, query=None, commitment_id=None):
        job_id = _new_id("job")

        def insert(connection):
            connection.execute("""
                INSERT INTO research_job (id, kind, status, query, commitment_id)
                VALUES (%s, %s, 'QUEUED', %s, %s)
            """, (job_id, kind, query, commitment_id))

        connection = self._submission_connection.get()
        if connection is None:
            with self._connect() as own_connection:
                insert(own_connection)
        else:
            insert(connection)
        return job_id

    def get_job(self, job_id):
        with self._connect() as connection:
            job = connection.execute("""
                SELECT id AS job_id, status, result, error FROM research_job WHERE id = %s
            """, (job_id,)).fetchone()
        if job is None:
            return None
        return {key: value for key, value in job.items() if value is not None}

    def claim_next(self):
        with self._connect() as connection:
            job = connection.execute("""
                WITH next_job AS (
                    SELECT id
                    FROM research_job
                    WHERE status = 'QUEUED'
                       OR (status = 'RUNNING' AND claim_expires_at <= statement_timestamp())
                    ORDER BY created_at, id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE research_job j
                SET status = 'RUNNING', claim_token = %s,
                    claim_expires_at = clock_timestamp() + make_interval(secs => %s),
                    updated_at = clock_timestamp()
                FROM next_job
                WHERE j.id = next_job.id
                RETURNING j.id AS job_id, j.claim_token, j.kind, j.query, j.commitment_id
            """, (str(uuid4()), self._lease_seconds)).fetchone()
        if job is None:
            return None
        return {key: value for key, value in job.items() if value is not None}

    def renew_claim(self, claim):
        with self._connect() as connection:
            renewal = connection.execute("""
                UPDATE research_job
                SET claim_expires_at = clock_timestamp() + make_interval(secs => %s),
                    updated_at = clock_timestamp()
                WHERE id = %s AND status = 'RUNNING' AND claim_token = %s
                  AND claim_expires_at > clock_timestamp()
                RETURNING id
            """, (self._lease_seconds, claim["job_id"], claim["claim_token"])).fetchone()
        if renewal is None:
            raise StaleClaim(claim["job_id"])

    @contextmanager
    def keep_claim_alive(self, claim):
        stopped = Event()

        def heartbeat():
            while not stopped.wait(self._lease_seconds / 3):
                try:
                    self.renew_claim(claim)
                except StaleClaim:
                    return
                except psycopg.Error:
                    LOG.warning("Could not renew a research job lease.")

        worker = Thread(target=heartbeat, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stopped.set()
            worker.join(timeout=1)

    @staticmethod
    def _running_job(connection, claim):
        job = connection.execute("""
            SELECT id, kind, commitment_id
            FROM research_job
            WHERE id = %s AND status = 'RUNNING' AND claim_token = %s
              AND claim_expires_at > clock_timestamp()
            FOR UPDATE
        """, (claim["job_id"], claim["claim_token"])).fetchone()
        if job is None:
            raise StaleClaim(claim["job_id"])
        return job

    @staticmethod
    def _set_terminal(connection, job, status, *, result=None, error=None):
        connection.execute("""
            UPDATE research_job
            SET status = %s, claim_token = NULL, claim_expires_at = NULL,
                result = %s, error = %s, updated_at = clock_timestamp()
            WHERE id = %s
        """, (status, Jsonb(result) if result is not None else None,
              Jsonb(error) if error is not None else None, job["id"]))

    @staticmethod
    def _source_id(connection, draft):
        requested_id = draft.get("source_id")
        url = draft.get("url")

        def existing():
            return connection.execute("""
                SELECT id FROM source
                WHERE id = %s OR url = %s
                ORDER BY (id = %s) DESC NULLS LAST
                LIMIT 1
            """, (requested_id, url, requested_id)).fetchone()

        found = existing()
        if found is not None:
            return found["id"]
        source_id = requested_id or _new_id("source")
        inserted = connection.execute("""
            INSERT INTO source (id, title, url, citation, publisher, source_type, published_on)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id
        """, (source_id, *(draft.get(field) for field in SOURCE_FIELDS))).fetchone()
        if inserted is not None:
            return inserted["id"]
        found = existing()
        if found is None:
            raise ValueError("Could not link the research source.")
        return found["id"]

    @staticmethod
    def _organization_id(connection, draft):
        requested_id = draft.get("organization_id")
        name = draft.get("organization_name")
        if requested_id and connection.execute(
            "SELECT id FROM organization WHERE id = %s", (requested_id,),
        ).fetchone() is not None:
            return requested_id
        if not name:
            return None
        matches = connection.execute(
            "SELECT id FROM organization WHERE lower(name) = lower(%s) ORDER BY id LIMIT 2", (name,),
        ).fetchall()
        if len(matches) == 1:
            return matches[0]["id"]
        if matches:
            return None
        organization_id = requested_id or _new_id("org")
        connection.execute(
            "INSERT INTO organization (id, name) VALUES (%s, %s)", (organization_id, name),
        )
        return organization_id

    def _save_run(self, connection, job, commitment_id, result):
        run_id = _new_id("run")
        connection.execute("""
            INSERT INTO research_run (
                id, commitment_id, job_id, verification_date, verdict, reasoning, completed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())
        """, (run_id, commitment_id, job["id"], result["verification_date"],
              result["verdict"], result["reasoning"]))
        evidence = sorted(
            result["evidence"],
            key=lambda item: (item["source"].get("url") or "", item["source"].get("source_id") or ""),
        )
        for draft in evidence:
            if not draft.get("retrieved_at"):
                raise ValueError("Evidence from a live run requires a retrieval date.")
            source_id = self._source_id(connection, draft["source"])
            connection.execute("""
                INSERT INTO evidence (
                    id, run_id, source_id, status, explanation, excerpt, summary, locator, retrieved_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (_new_id("evidence"), run_id, source_id, *(draft.get(field) for field in EVIDENCE_FIELDS)))
        connection.execute(
            "UPDATE research_run SET completed_at = clock_timestamp() WHERE id = %s", (run_id,),
        )
        ids = {"commitment_id": commitment_id, "run_id": run_id}
        self._set_terminal(connection, job, "COMPLETED", result={"outcome": "RESOLVED", **ids})
        return {"outcome": "COMMITTED", **ids}

    def finish_discovery(self, claim, expected_catalog_revision, draft, result):
        with self._connect() as connection:
            job = self._running_job(connection, claim)
            if job["kind"] != "DISCOVER":
                raise ValueError("The job does not represent a discovery.")
            catalog = connection.execute("""
                SELECT revision FROM tracegraph_catalog WHERE singleton FOR UPDATE
            """).fetchone()
            if str(catalog["revision"]) != expected_catalog_revision:
                return {"outcome": "RECHECK_REQUIRED"}
            promise = draft.get("original_promise")
            if not promise or not (promise.get("locator") and promise.get("excerpt")):
                raise ValueError("A new commitment requires a source, locator, and original excerpt.")
            embedding = _vector(draft["embedding"])
            source_id = self._source_id(connection, promise["source"])
            organization_id = self._organization_id(connection, draft)
            beneficiary = draft.get("beneficiary") or {}
            commitment_id = _new_id("commitment")
            connection.execute("""
                INSERT INTO commitment (
                    id, organization_id, title, description, original_text, responsible_entity_raw,
                    beneficiary_category, beneficiary_named, obligation_type, amount_value,
                    term_periodicity, geographic_scope, condition_contingency, milestone, notes,
                    original_source_id, original_locator, embedding
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector
                )
            """, (
                commitment_id, organization_id, draft.get("title"), draft["description"],
                draft.get("original_text") or promise["excerpt"], draft.get("responsible_entity_raw"),
                beneficiary.get("category"), beneficiary.get("name"), draft.get("obligation_type"),
                draft.get("amount_value"), draft.get("term_periodicity"), draft.get("geographic_scope"),
                draft.get("condition_contingency"), draft.get("milestone"), draft.get("notes"),
                source_id, promise["locator"], embedding,
            ))
            return self._save_run(connection, job, commitment_id, result)

    def finish_refresh(self, claim, result):
        with self._connect() as connection:
            job = self._running_job(connection, claim)
            if job["kind"] != "REFRESH":
                raise ValueError("The job does not represent a refresh.")
            return self._save_run(connection, job, job["commitment_id"], result)

    def finish_without_run(self, claim, outcome):
        with self._connect() as connection:
            job = self._running_job(connection, claim)
            if job["kind"] != "DISCOVER":
                raise ValueError("A refresh cannot complete without a run.")
            self._set_terminal(connection, job, "COMPLETED", result=outcome)

    def fail(self, claim, error):
        with self._connect() as connection:
            job = self._running_job(connection, claim)
            self._set_terminal(connection, job, "FAILED", error=error)
