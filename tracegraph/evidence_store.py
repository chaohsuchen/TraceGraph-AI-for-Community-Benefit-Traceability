"""PostgreSQL store for a commitment's consolidated evidence and assessment."""

from hashlib import sha256
from json import dumps

from tracegraph.data import EVIDENCE_FIELDS
from tracegraph.postgres import PostgresStore, _new_id


def _normalize(text):
    return " ".join((text or "").split())


def _key(*parts):
    return sha256(dumps(parts, ensure_ascii=False).encode("utf-8")).digest()


class EvidenceStore(PostgresStore):
    """Keep evidence across refreshes and update a single assessment per commitment."""

    @staticmethod
    def _assessment(connection, commitment_id):
        row = connection.execute("""
            SELECT revision, label, verdict, reasoning, to_jsonb(verification_date) AS verification_date
            FROM commitment_assessment
            WHERE commitment_id = %s
        """, (commitment_id,)).fetchone()
        revision = row.pop("revision") if row else 0
        return revision, row

    @classmethod
    def _research_snapshot(cls, connection, commitment_id):
        revision, assessment = cls._assessment(connection, commitment_id)
        findings = connection.execute("""
            SELECT f.id, f.claim,
                COALESCE(jsonb_agg(
                    jsonb_build_object(
                        'id', e.id, 'source', to_jsonb(s), 'status', e.status,
                        'explanation', e.explanation, 'excerpt', e.excerpt,
                        'summary', e.summary, 'locator', e.locator, 'retrieved_at', e.retrieved_at
                    )
                    ORDER BY e.first_seen_at, e.id
                ) FILTER (WHERE e.id IS NOT NULL), '[]'::jsonb) AS supports
            FROM evidence_finding f
            LEFT JOIN evidence_support e ON e.commitment_id = f.commitment_id AND e.finding_id = f.id
            LEFT JOIN source s ON s.id = e.source_id
            WHERE f.commitment_id = %s
            GROUP BY f.commitment_id, f.id
            ORDER BY f.first_seen_at, f.id
        """, (commitment_id,)).fetchall()
        relations = connection.execute("""
            SELECT kind, from_id, to_id, reasoning, status, preferred_id
            FROM evidence_relation WHERE commitment_id = %s ORDER BY kind, from_id, to_id
        """, (commitment_id,)).fetchall()
        return {"revision": revision, "assessment": assessment, "findings": findings, "relations": relations}

    def research_snapshot(self, commitment_id):
        with self._read_snapshot() as connection:
            if connection.execute(
                "SELECT id FROM commitment WHERE id = %s", (commitment_id,),
            ).fetchone() is None:
                return None
            return self._research_snapshot(connection, commitment_id)

    def graph(self, commitment_id, run_id=None):
        if run_id is not None:
            return None
        with self._read_snapshot() as connection:
            graph = self._load_commitment(connection, commitment_id)
            if graph is None:
                return None
            snapshot = self._research_snapshot(connection, commitment_id)
            snapshot.pop("revision")
            return {**graph, **snapshot}

    def finish_refresh(self, claim, result):
        with self._connect() as connection:
            job = self._running_job(connection, claim)
            if job["kind"] != "REFRESH":
                raise ValueError("The job does not represent a refresh.")
            commitment_id = job["commitment_id"]
            connection.execute("SELECT id FROM commitment WHERE id = %s FOR NO KEY UPDATE", (commitment_id,))
            revision, _ = self._assessment(connection, commitment_id)
            if self._expected_revision(result) != revision:
                return {"outcome": "RECHECK_REQUIRED"}
            return self._save_assessment(connection, job, commitment_id, revision, result)

    def _save_run(self, connection, job, commitment_id, result):
        # The inherited discovery transaction has just inserted this commitment.
        if self._expected_revision(result) != 0:
            raise ValueError("A new commitment must start at evidence revision zero.")
        return self._save_assessment(connection, job, commitment_id, 0, result)

    @staticmethod
    def _expected_revision(result):
        revision = result.get("expected_revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("Evidence revision must be a nonnegative integer.")
        return revision

    def _save_assessment(self, connection, job, commitment_id, revision, result):
        if not result.get("verification_date"):
            raise ValueError("A live assessment requires a verification date.")
        aliases, known_ids = self._merge_findings(connection, commitment_id, result["findings"])
        self._merge_relations(connection, commitment_id, result["relations"], aliases, known_ids)
        for first, second in result.get("dismissed_conflicts", ()):
            connection.execute("""
                DELETE FROM evidence_relation
                WHERE commitment_id = %s AND kind = 'CONFLICT'
                    AND from_id = %s AND to_id = %s
            """, (commitment_id, first, second))
        connection.execute("""
            INSERT INTO commitment_assessment (commitment_id, revision, label, verdict, reasoning, verification_date)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (commitment_id) DO UPDATE SET
                revision = EXCLUDED.revision, label = EXCLUDED.label, verdict = EXCLUDED.verdict,
                reasoning = EXCLUDED.reasoning, verification_date = EXCLUDED.verification_date,
                updated_at = clock_timestamp()
        """, (commitment_id, revision + 1, result["label"], result["verdict"],
              result["reasoning"], result["verification_date"]))
        ids = {"commitment_id": commitment_id, "run_id": None}
        self._set_terminal(connection, job, "COMPLETED", result={"outcome": "RESOLVED", **ids})
        return {"outcome": "COMMITTED", **ids}

    def _merge_findings(self, connection, commitment_id, findings):
        saved = connection.execute("""
            SELECT id, claim_key FROM evidence_finding WHERE commitment_id = %s
        """, (commitment_id,)).fetchall()
        keys_by_id = {item["id"]: item["claim_key"] for item in saved}
        ids_by_key = {item["claim_key"]: item["id"] for item in saved}
        aliases = {}
        supports = []
        for finding in findings:
            finding_id = finding["id"]
            claim = _normalize(finding["claim"])
            if not finding_id.strip() or not claim:
                raise ValueError("A finding requires a stable ID and a claim.")
            key = _key(claim)
            canonical_id = ids_by_key.get(key)
            if finding_id in keys_by_id and keys_by_id[finding_id] != key:
                raise ValueError("A finding ID cannot be reused for a different claim.")
            if finding_id in aliases and aliases[finding_id] != canonical_id:
                raise ValueError("A finding ID cannot identify multiple claims.")
            if canonical_id is None:
                if not finding["supports"]:
                    raise ValueError("A new finding requires source support.")
                canonical_id = finding_id
                connection.execute("""
                    INSERT INTO evidence_finding (commitment_id, id, claim, claim_key)
                    VALUES (%s, %s, %s, %s)
                """, (commitment_id, canonical_id, finding["claim"], key))
                keys_by_id[canonical_id] = key
                ids_by_key[key] = canonical_id
            else:
                connection.execute("""
                    UPDATE evidence_finding SET last_seen_at = clock_timestamp()
                    WHERE commitment_id = %s AND id = %s
                """, (commitment_id, canonical_id))
            aliases[finding_id] = canonical_id
            supports.extend((canonical_id, item) for item in finding["supports"])

        # Keep shared source lookups in a consistent order across concurrent refreshes.
        supports.sort(key=lambda item: (
            item[1]["source"].get("url") or "",
            item[1]["source"].get("source_id") or item[1]["source"].get("id") or "",
        ))
        for finding_id, support in supports:
            self._merge_support(connection, commitment_id, finding_id, support)
        return aliases, set(keys_by_id)

    def _merge_support(self, connection, commitment_id, finding_id, support):
        source = support["source"]
        if source.get("id") and not source.get("source_id"):
            source = {**source, "source_id": source["id"]}
        source_id = self._source_id(connection, source)
        locator = _normalize(support.get("locator"))
        text = _normalize(support.get("excerpt")) or _normalize(support.get("summary"))
        citation_key = _key(locator, text)
        connection.execute("""
            INSERT INTO evidence_support (
                id, commitment_id, finding_id, source_id, citation_key, status, explanation,
                excerpt, summary, locator, retrieved_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (commitment_id, finding_id, source_id, citation_key) DO UPDATE
            SET last_seen_at = clock_timestamp()
        """, (_new_id("support"), commitment_id, finding_id, source_id, citation_key,
              *(support.get(field) for field in EVIDENCE_FIELDS)))

    @staticmethod
    def _merge_relations(connection, commitment_id, relations, aliases, known_ids):
        check_progress_cycles = False
        for relation in relations:
            from_id, to_id = relation["from_id"], relation["to_id"]
            from_id, to_id = aliases.get(from_id, from_id), aliases.get(to_id, to_id)
            if from_id not in known_ids or to_id not in known_ids or from_id == to_id:
                raise ValueError("A relation must connect two different findings of this commitment.")
            kind, status = relation["kind"], relation.get("status")
            preferred = relation.get("preferred_id")
            preferred = aliases.get(preferred, preferred)
            if kind == "PROGRESS":
                if status is not None or preferred is not None:
                    raise ValueError("Progress relations cannot declare conflict resolution.")
                check_progress_cycles = True
            elif kind == "CONFLICT":
                if not ((status == "RESOLVED" and preferred in (from_id, to_id))
                        or (status == "UNRESOLVED" and preferred is None)):
                    raise ValueError("Conflict resolution requires a valid preferred finding.")
                reverse = connection.execute("""
                    SELECT 1 FROM evidence_relation
                    WHERE commitment_id = %s AND kind = 'CONFLICT' AND from_id = %s AND to_id = %s
                """, (commitment_id, to_id, from_id)).fetchone()
                if reverse is not None:
                    from_id, to_id = to_id, from_id
            else:
                raise ValueError("Unsupported evidence relation kind.")
            connection.execute("""
                INSERT INTO evidence_relation (commitment_id, kind, from_id, to_id, reasoning, status, preferred_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (commitment_id, kind, from_id, to_id) DO UPDATE SET
                    reasoning = EXCLUDED.reasoning, status = EXCLUDED.status, preferred_id = EXCLUDED.preferred_id
            """, (commitment_id, kind, from_id, to_id, relation["reasoning"], status, preferred))

        if check_progress_cycles and connection.execute("""
            WITH RECURSIVE progress (from_id, to_id) AS (
                SELECT from_id, to_id FROM evidence_relation WHERE commitment_id = %s AND kind = 'PROGRESS'
                UNION
                SELECT p.from_id, r.to_id
                FROM progress p JOIN evidence_relation r ON r.from_id = p.to_id
                WHERE r.commitment_id = %s AND r.kind = 'PROGRESS'
            )
            SELECT 1 FROM progress WHERE from_id = to_id LIMIT 1
        """, (commitment_id, commitment_id)).fetchone() is not None:
            raise ValueError("Progress relations cannot form a cycle.")
