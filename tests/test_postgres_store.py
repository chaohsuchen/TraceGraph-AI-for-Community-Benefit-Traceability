import os
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from json import dumps
from pathlib import Path
from unittest import TestCase, skipUnless
from uuid import uuid4


DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


@skipUnless(DATABASE_URL and find_spec("psycopg"), "Set TEST_DATABASE_URL and install psycopg to test PostgreSQL")
class PostgresStoreTests(TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from tracegraph.postgres import PostgresStore

        schema = f"tracegraph_test_{uuid4().hex}"
        cls.dsn = make_conninfo(DATABASE_URL, options=f"-c search_path={schema},public")
        cls.store = PostgresStore(cls.dsn)
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def cleanup():
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

        cls.addClassCleanup(cleanup)
        with psycopg.connect(cls.dsn, autocommit=True) as connection:
            for name in ("001_initial.sql", "002_research_job_leases.sql"):
                migration = (ROOT / "db" / name).read_text(encoding="utf-8")
                connection.execute(migration, prepare=False)

    def setUp(self):
        with self.store._connect() as connection:
            connection.execute("""
                TRUNCATE api_idempotency, evidence, research_run, research_job,
                         commitment, source, organization, workbook_annotation
            """)
            connection.execute("""
                UPDATE tracegraph_catalog SET revision = 0, embedding_model = 'test',
                                              embedding_dimensions = 2
            """)
            connection.execute("""
                INSERT INTO organization (id, name) VALUES ('org-bally', 'Bally’s Corporation')
            """)
            connection.execute("""
                INSERT INTO source (id, title, url, published_on)
                VALUES ('source-board', 'Board report', 'https://example.org/board', '2025-12-01')
            """)
            connection.execute("""
                INSERT INTO commitment (
                    id, organization_id, description, original_source_id, original_locator,
                    original_document_id, original_char_start, original_char_end, embedding
                ) VALUES
                    ('TG-GOLD-H008', 'org-bally', 'Public safety', 'source-board', 'p.13',
                     'GFLB', 3, 7, '[1,0]'::vector),
                    ('TG-GOLD-H010', 'org-bally', 'Renewable energy', 'source-board', 'p.14',
                     'GFLB', 8, 20, '[0,1]'::vector),
                    ('TG-GOLD-H001', 'org-bally', 'Permanent jobs', NULL, NULL,
                     NULL, NULL, NULL, '[-1,0]'::vector)
            """)
            connection.execute("""
                INSERT INTO research_run (id, commitment_id, verdict, reasoning) VALUES
                    ('import-h008', 'TG-GOLD-H008', 'Confirmed', 'Details on page 13'),
                    ('import-h010', 'TG-GOLD-H010', 'Target documented', 'Details on page 14')
            """)
            connection.execute("""
                INSERT INTO evidence (id, run_id, source_id, status, locator) VALUES
                    ('h008-ev', 'import-h008', 'source-board', 'Pledge documented', 'p.13'),
                    ('h010-ev', 'import-h010', 'source-board', 'Target documented', 'p.14')
            """)

    @staticmethod
    def _result():
        return {
            "verification_date": "2026-11-07",
            "verdict": "Evidence located",
            "reasoning": "A new run located the original commitment document.",
            "evidence": [{
                "source": {"title": "Board report", "url": "https://example.org/board"},
                "status": "Documented", "summary": "The report lists the obligation.",
                "locator": "p.13", "retrieved_at": "2026-11-07T12:00:00Z",
            }],
        }

    def test_replays_concurrent_submission_and_rolls_back_failed_acceptance(self):
        from tracegraph.api import accepted
        from tracegraph.data import IdempotencyConflict
        from tracegraph.postgres import PostgresStore

        def submit(_):
            return self.store.submit("lookup", "same-key", {"query": "example"},
                                     lambda: accepted(self.store.enqueue("DISCOVER", query="example")))

        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(submit, range(12)))
        self.assertTrue(all(response == responses[0] for response in responses))
        job_id = responses[0][2]["job_id"]
        reopened = PostgresStore(self.dsn)
        self.assertEqual(reopened.get_job(job_id), {"job_id": job_id, "status": "QUEUED"})
        with self.assertRaises(IdempotencyConflict):
            reopened.submit("lookup", "same-key", {"query": "different"}, lambda: self.fail("replayed"))

        def failed_action():
            self.store.enqueue("DISCOVER", query="not accepted")
            raise ValueError("response construction failed")

        with self.assertRaises(ValueError):
            self.store.submit("lookup", "failing-key", {"query": "not accepted"}, failed_action)
        self.assertEqual(self.store.claim_next()["job_id"], job_id)
        self.assertIsNone(self.store.claim_next())

    def test_repeatable_search_and_atomic_discovery_revision(self):
        def inspect(snapshot):
            self.assertEqual(snapshot.vector_top_k([1, 0], 2), [
                {"commitment_id": "TG-GOLD-H008", "rank": 1},
                {"commitment_id": "TG-GOLD-H010", "rank": 2},
            ])
            with self.store._connect() as connection:
                connection.execute("""
                    INSERT INTO commitment (id, description, embedding)
                    VALUES ('arrived', 'Another obligation', '[1,0]'::vector)
                """)
            self.assertNotIn("arrived", {item["commitment_id"] for item in snapshot.scan()})
            return snapshot.catalog_revision

        stale_revision = self.store.with_snapshot(inspect)
        current_revision = self.store.with_snapshot(lambda snapshot: snapshot.catalog_revision)
        self.assertNotEqual(stale_revision, current_revision)
        self.store.enqueue("DISCOVER", query="new obligation")
        claim = self.store.claim_next()
        draft = {
            "description": "Another test commitment", "organization_name": "Bally’s Corporation",
            "embedding": [1, 0],
            "original_promise": {
                "source": {"title": "Original promise", "citation": "Mayoral statement"},
                "locator": "section 4", "excerpt": "We will provide the pilot.",
            },
        }
        self.assertEqual(self.store.finish_discovery(claim, stale_revision, draft, self._result()),
                         {"outcome": "RECHECK_REQUIRED"})
        committed = self.store.finish_discovery(claim, current_revision, draft, self._result())
        graph = self.store.graph(committed["commitment_id"])
        self.assertEqual(graph["organization"]["id"], "org-bally")
        self.assertEqual(graph["commitment"]["original_promise"]["source"]["citation"], "Mayoral statement")
        self.assertEqual(self.store.get_job(claim["job_id"])["result"]["run_id"], committed["run_id"])
        self.assertIn(committed["commitment_id"],
                      self.store.with_snapshot(lambda snap: {row["commitment_id"] for row in snap.scan()}))

    def test_graph_refresh_history_and_rollback(self):
        cid = "TG-GOLD-H008"
        before = self.store.graph(cid)
        other = self.store.graph("TG-GOLD-H010")
        empty = self.store.graph("TG-GOLD-H001")
        self.assertIsNone(empty["latest_completed_run"])
        self.assertEqual(before["source_edges"][0]["source"], other["source_edges"][0]["source"])
        self.assertNotEqual(before["source_edges"][0]["evidence"], other["source_edges"][0]["evidence"])
        self.assertIsNone(before["latest_completed_run"]["verification_date"])

        self.store.enqueue("REFRESH", commitment_id=cid)
        claim = self.store.claim_next()
        self.store.renew_claim(claim)
        completed = self.store.finish_refresh(claim, self._result())
        new_graph = self.store.graph(cid)
        self.assertEqual(new_graph["viewed_run_id"], completed["run_id"])
        self.assertEqual(new_graph["latest_completed_run"]["verification_date"], "2026-11-07")
        self.assertEqual(new_graph["source_edges"][0]["source"]["id"], "source-board")
        self.assertEqual(new_graph["source_edges"][0]["source"]["published_on"], "2025-12-01")
        self.assertIsInstance(dumps(new_graph), str)
        history = self.store.graph(cid, "import-h008")
        self.assertEqual(history["source_edges"], before["source_edges"])
        self.assertEqual(history["latest_completed_run"], new_graph["latest_completed_run"])
        self.assertIsNone(self.store.graph("TG-GOLD-H010", "import-h008"))
        organization = self.store.organization_commitments("org-bally")
        self.assertEqual({row["id"] for row in organization["commitments"]},
                         {"TG-GOLD-H001", "TG-GOLD-H008", "TG-GOLD-H010"})

        self.store.enqueue("REFRESH", commitment_id=cid)
        failed = self.store.claim_next()
        result = self._result()
        result["evidence"][0]["retrieved_at"] = None
        with self.assertRaises(ValueError):
            self.store.finish_refresh(failed, result)
        self.store.fail(failed, {"code": "INTERNAL_ERROR", "message": "Test failure", "retryable": True})
        self.assertEqual(self.store.get_job(failed["job_id"])["status"], "FAILED")
        self.assertEqual(self.store.graph(cid), new_graph)

    def test_expired_claim_can_be_recovered_by_another_worker(self):
        from tracegraph.data import StaleClaim
        from tracegraph.postgres import PostgresStore

        job_id = self.store.enqueue("DISCOVER", query="recoverable")
        original = self.store.claim_next()
        with self.store._connect() as connection:
            connection.execute("""
                UPDATE research_job SET claim_expires_at = clock_timestamp() - interval '1 second'
                WHERE id = %s
            """, (job_id,))
        reopened = PostgresStore(self.dsn)
        reclaimed = reopened.claim_next()
        self.assertEqual(reclaimed["job_id"], job_id)
        self.assertNotEqual(original["claim_token"], reclaimed["claim_token"])
        outcome = {"outcome": "RESOLVED", "commitment_id": "TG-GOLD-H008", "run_id": None}
        with self.assertRaises(StaleClaim):
            self.store.finish_without_run(original, outcome)
        reopened.finish_without_run(reclaimed, outcome)
        self.assertEqual(self.store.get_job(job_id)["result"], outcome)
