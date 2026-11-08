"""Exercise consolidated evidence writes against PostgreSQL and pgvector."""

from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
import os
from pathlib import Path
from unittest import TestCase, skipUnless

from tests.db_support import isolated_database


DATABASE_URL = os.environ.get("TRACEGRAPH_TEST_DSN") or os.environ.get("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]
CID = "TG-GOLD-H008"


def support(name="board", summary="The report documents the promise."):
    return {
        "source": {"title": name, "url": f"https://example.org/{name}", "published_on": "2026-10-01"},
        "summary": summary, "status": "DOCUMENTED", "explanation": "The source states the promise.",
        "locator": "section 3", "retrieved_at": "2026-11-07T12:00:00Z",
    }


def finding(finding_id, claim, *supports):
    return {"id": finding_id, "claim": claim, "supports": list(supports)}


def relation(kind, first, second, status=None, preferred=None):
    return {
        "kind": kind, "from_id": first, "to_id": second,
        "reasoning": "The source reports support this relation.",
        "status": status, "preferred_id": preferred,
    }


def result(revision, findings=(), relations=(), verification_date="2026-11-07"):
    return {
        "expected_revision": revision, "verification_date": verification_date,
        "label": "UNVERIFIED", "verdict": "Original promise documented",
        "reasoning": "The sources confirm the promise. Fulfillment is unverified.",
        "findings": list(findings), "relations": list(relations),
    }


@skipUnless(DATABASE_URL and find_spec("psycopg"), "Set TRACEGRAPH_TEST_DSN and install psycopg.")
class EvidenceStoreTests(TestCase):
    def setUp(self):
        import psycopg
        from tracegraph.evidence_store import EvidenceStore

        scope = isolated_database(DATABASE_URL)
        dsn = scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)
        self.store = EvidenceStore(dsn)
        with psycopg.connect(dsn, autocommit=True) as connection:
            for name in ("001_initial.sql", "002_research_job_leases.sql", "003_evidence_graph.sql"):
                connection.execute((ROOT / "db" / name).read_text(encoding="utf-8"), prepare=False)
            connection.execute("""
                UPDATE tracegraph_catalog SET embedding_model = 'test', embedding_dimensions = 2;
                INSERT INTO organization (id, name) VALUES ('org-bally', 'Bally’s Corporation');
                INSERT INTO commitment (id, organization_id, description, embedding)
                VALUES ('TG-GOLD-H008', 'org-bally', 'Public safety promise', '[1,0]'::vector);
            """, prepare=False)

    def _claim(self, commitment_id=CID):
        self.store.enqueue("REFRESH", commitment_id=commitment_id)
        return self.store.claim_next()

    def test_initial_snapshot_and_discovery_use_no_legacy_runs(self):
        empty = {"revision": 0, "assessment": None, "findings": [], "relations": []}
        self.assertEqual(self.store.research_snapshot(CID), empty)
        self.assertIsNone(self.store.research_snapshot("missing"))
        self.assertEqual(self.store.graph(CID)["assessment"], None)
        self.assertEqual(self.store.graph(CID)["organization"]["id"], "org-bally")

        catalog_revision = self.store.with_snapshot(lambda snapshot: snapshot.catalog_revision)
        self.store.enqueue("DISCOVER", query="Was a transit pilot promised?")
        claim = self.store.claim_next()
        promise_support = support("statement")
        draft = {
            "description": "The authority will offer a transit pilot.", "embedding": [0, 1],
            "original_promise": {
                "source": promise_support["source"], "locator": promise_support["locator"],
                "excerpt": "The authority will offer a transit pilot.",
            },
        }
        assessment = result(0, [finding("promise", "The pilot was promised.", promise_support)])
        self.assertEqual(self.store.finish_discovery(claim, "stale", draft, assessment),
                         {"outcome": "RECHECK_REQUIRED"})
        saved = self.store.finish_discovery(claim, catalog_revision, draft, assessment)
        self.assertEqual(saved["outcome"], "COMMITTED")
        cid = saved["commitment_id"]
        self.assertIsNone(saved["run_id"])
        graph = self.store.graph(cid)
        self.assertEqual(set(graph), {"commitment", "organization", "assessment", "findings", "relations"})
        self.assertEqual(graph["assessment"]["verification_date"], "2026-11-07")
        self.assertEqual(graph["findings"][0]["supports"][0]["source"],
                         graph["commitment"]["original_promise"]["source"])
        self.assertEqual(self.store.research_snapshot(cid)["revision"], 1)
        self.assertEqual(self.store.get_job(claim["job_id"])["result"],
                         {"outcome": "RESOLVED", "commitment_id": cid, "run_id": None})
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM research_run").fetchone()["count"], 0)

    def test_merge_retains_original_nodes_supports_and_relation(self):
        first = result(0, [
            finding("promise", "Public safety funding was promised.", support()),
            finding("reported", "A report disputes the funding amount.", support("review")),
        ], [relation("CONFLICT", "promise", "reported", "UNRESOLVED")])
        self.store.finish_refresh(self._claim(), first)
        before = self.store.research_snapshot(CID)
        original = before["findings"][0]["supports"][0]

        repeated = {
            **original, "retrieved_at": "2026-11-08T12:00:00Z",
            "summary": "The report documents  the promise.",
        }
        second = result(1, [
            finding("alias", "Public safety funding was promised.", repeated, support("news")),
        ], [relation("CONFLICT", "reported", "alias", "RESOLVED", "alias")], "2026-11-08")
        self.store.finish_refresh(self._claim(), second)
        after = self.store.research_snapshot(CID)
        self.assertEqual(after["revision"], 2)
        self.assertEqual(after["assessment"]["verification_date"], "2026-11-08")
        self.assertEqual([item["id"] for item in after["findings"]], ["promise", "reported"])
        self.assertEqual(after["findings"][0]["supports"][0], original)
        self.assertEqual(len(after["findings"][0]["supports"]), 2)
        self.assertEqual(after["findings"][1], before["findings"][1])
        self.assertEqual(after["relations"], [relation("CONFLICT", "promise", "reported", "RESOLVED", "promise")])
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM commitment_assessment").fetchone()["count"], 1)
            times = connection.execute("""
                SELECT last_seen_at > first_seen_at AS updated FROM evidence_support WHERE id = %s
            """, (original["id"],)).fetchone()
            self.assertTrue(times["updated"])

    def test_stale_concurrent_refresh_rechecks_before_writing(self):
        claims = (self._claim(), self._claim())
        pending = [
            result(0, [finding(f"claim-{index}", f"Different observation {index}", support(f"source-{index}"))])
            for index in range(2)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(self.store.finish_refresh, claims, pending))
        self.assertCountEqual([value["outcome"] for value in outcomes], ["COMMITTED", "RECHECK_REQUIRED"])
        saved = self.store.research_snapshot(CID)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(len(saved["findings"]), 1)
        loser = outcomes.index({"outcome": "RECHECK_REQUIRED"})
        self.assertEqual(self.store.get_job(claims[loser]["job_id"])["status"], "RUNNING")
        with self.store._connect() as connection:
            urls = connection.execute("SELECT url FROM source").fetchall()
            self.assertEqual(len(urls), 1)
            self.assertNotIn(f"https://example.org/source-{loser}", [row["url"] for row in urls])

    def test_invalid_or_cyclic_relation_rolls_back_entire_refresh(self):
        self.store.finish_refresh(self._claim(), result(0, [
            finding("a", "Original promise", support("a")),
            finding("b", "Additional budget announced", support("b")),
            finding("c", "Contract signed", support("c")),
        ], [relation("PROGRESS", "a", "b"), relation("PROGRESS", "b", "c")]))
        baseline = self.store.research_snapshot(CID)

        claim = self._claim()
        new_support = finding("c", "Contract signed", support("new"))
        for bad in (
            relation("PROGRESS", "c", "a"),
            relation("PROGRESS", "a", "missing"),
            relation("CONFLICT", "a", "b", "RESOLVED"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.store.finish_refresh(claim, result(1, [new_support], [bad]))
                self.assertEqual(self.store.research_snapshot(CID), baseline)
        self.store.fail(claim, {"code": "INTERNAL_ERROR", "message": "Test failure", "retryable": True})
        self.assertEqual(self.store.research_snapshot(CID), baseline)
        with self.store._connect() as connection:
            source = connection.execute("SELECT id FROM source WHERE url = 'https://example.org/new'").fetchone()
            self.assertIsNone(source)
