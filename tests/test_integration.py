"""Exercise the assembled evidence graph in a clean PostgreSQL catalog."""

import json
import os
from unittest import TestCase, skipUnless

from tests.db_support import isolated_database
from tracegraph.runtime import create_application, prepare_catalog
from tracegraph.runtime_fixtures import ANNOUNCEMENT, AUDIT, BUDGET, FIXTURE_QUERY, ORIGINAL


@skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a disposable PostgreSQL instance")
class FullIntegrationTest(TestCase):
    def test_empty_catalog_to_consolidated_evidence_graph(self):
        import psycopg

        from tracegraph.evidence_store import EvidenceStore

        with isolated_database(os.environ["TEST_DATABASE_URL"]) as dsn:
            self.assertEqual(prepare_catalog(dsn)["commitments"], 0)
            self.assertEqual(prepare_catalog(dsn)["commitments"], 0)
            with psycopg.connect(dsn) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM workbook_annotation").fetchone()[0], 0)

            app = create_application(dsn, "fixture")

            def call(method, path, key=None, body=None):
                return app.respond(
                    method, "/api/v1" + path, {"Idempotency-Key": key} if key else {},
                    json.dumps(body).encode() if body else b"",
                )

            def lookup(query, key):
                return call("POST", "/lookup", key, {"query": query})

            def finish(accepted):
                self.assertEqual(accepted[0], 202, accepted)
                job_path = accepted[1]["Location"].removeprefix("/api/v1")
                self.assertEqual(call("GET", job_path)[2]["status"], "QUEUED")
                self.assertTrue(app.process_one_job())
                status, _, job = call("GET", job_path)
                self.assertEqual(status, 200)
                self.assertEqual(job["status"], "COMPLETED", job)
                return job["result"]

            def graph(path):
                status, _, data = call("GET", path)
                self.assertEqual(status, 200, data)
                return data

            started = lookup(FIXTURE_QUERY, "new")
            self.assertEqual(lookup(FIXTURE_QUERY, "new"), started)
            created = finish(started)
            cid = created["commitment_id"]
            self.assertEqual(created, {"outcome": "RESOLVED", "commitment_id": cid, "run_id": None})
            path = f"/commitments/{cid}"
            first = graph(path)
            self.assertEqual(set(first), {"commitment", "organization", "assessment", "findings", "relations"})
            self.assertEqual(first["assessment"]["label"], "UNVERIFIED")
            first_findings = {item["claim"]: item for item in first["findings"]}
            self.assertEqual(set(first_findings), {ORIGINAL, BUDGET})
            self.assertEqual(first["commitment"]["original_promise"]["source"],
                             first_findings[ORIGINAL]["supports"][0]["source"])
            self.assertEqual(lookup(FIXTURE_QUERY, "known")[2], {"outcome": "MATCH", "commitment_id": cid})

            org_id = first["organization"]["id"]
            expanded = call("GET", f"/organizations/{org_id}/commitments")[2]
            self.assertEqual([item["id"] for item in expanded["commitments"]], [cid])

            refresh_path = path + "/research-runs"
            accepted = call("POST", refresh_path, "refresh-1")
            self.assertEqual(call("POST", refresh_path, "refresh-1"), accepted)
            self.assertEqual(finish(accepted), created)
            self.assertEqual(call("POST", refresh_path, "refresh-1"), accepted)
            self.assertFalse(app.process_one_job())

            updated = graph(path)
            self.assertEqual(updated["assessment"]["label"], "PARTIALLY_CORROBORATED")
            findings = {item["claim"]: item for item in updated["findings"]}
            self.assertEqual(set(findings), {ORIGINAL, BUDGET, ANNOUNCEMENT, AUDIT})
            self.assertEqual(findings[ORIGINAL], first_findings[ORIGINAL])
            self.assertEqual(findings[BUDGET], first_findings[BUDGET])
            self.assertEqual({item["source"]["title"] for item in findings[AUDIT]["supports"]},
                             {"Independent audit", "Vehicle deployment register"})
            conflicts = [item for item in updated["relations"] if item["kind"] == "CONFLICT"]
            self.assertEqual(len(conflicts), 1)
            conflict = conflicts[0]
            self.assertEqual({conflict["from_id"], conflict["to_id"]},
                             {findings[AUDIT]["id"], findings[ANNOUNCEMENT]["id"]})
            self.assertEqual((conflict["status"], conflict["preferred_id"]),
                             ("RESOLVED", findings[AUDIT]["id"]))
            self.assertIn("vehicle register", conflict["reasoning"])
            self.assertEqual(sum(item["kind"] == "PROGRESS" for item in updated["relations"]), 3)

            repeated = call("POST", refresh_path, "refresh-2")
            self.assertNotEqual(repeated[2]["job_id"], accepted[2]["job_id"])
            self.assertEqual(finish(repeated), created)
            final = graph(path)
            self.assertEqual(final["findings"], updated["findings"])
            self.assertEqual(final["relations"], updated["relations"])
            self.assertEqual(final["assessment"]["label"], "PARTIALLY_CORROBORATED")
            self.assertEqual(EvidenceStore(dsn).graph(cid), final)
            self.assertEqual(finish(lookup("An unrelated pledge with no public record", "unknown"))["outcome"],
                             "NO_PUBLIC_PROMISE")
            with psycopg.connect(dsn) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM commitment").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM research_run").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM workbook_annotation").fetchone()[0], 0)
