"""Exercise the assembled backend with the actual workbook and pgvector."""

import json
import os
from unittest import TestCase, skipUnless

from tests.db_support import isolated_database
from tracegraph.runtime import WORKBOOK, apply_migrations, create_application


@skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a disposable PostgreSQL instance")
class FullIntegrationTest(TestCase):
    def test_workbook_to_lookup_discovery_refresh_and_history(self):
        import psycopg

        from tracegraph.evaluation import export_golden
        from tracegraph.postgres import PostgresStore
        from tracegraph.providers import LocalEmbedding
        from tracegraph.workbook_import import import_workbook

        with isolated_database(os.environ["TEST_DATABASE_URL"]) as dsn:
            apply_migrations(dsn)
            apply_migrations(dsn)
            embed = LocalEmbedding()
            with psycopg.connect(dsn) as connection:
                first = import_workbook(
                    connection, WORKBOOK, embed, embedding_model=embed.profile, embedding_dimensions=embed.dimensions,
                )
                again = import_workbook(
                    connection, WORKBOOK, embed, embedding_model=embed.profile, embedding_dimensions=embed.dimensions,
                )
            self.assertEqual(first["inserted_commitments"], 100)
            self.assertEqual(first["inserted_historical_runs"], 6)
            self.assertEqual(again["inserted_commitments"], 0)
            self.assertEqual(again["inserted_historical_runs"], 0)

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
                self.assertTrue(app.process_one_job())
                return call("GET", accepted[1]["Location"].removeprefix("/api/v1"))[2]

            funding = "/commitments/TG-GOLD-H008"
            energy = "/commitments/TG-GOLD-H010"
            status, _, matched = lookup("What did Bally’s promise for the Bronx police substation and public safety?", "funding")
            self.assertEqual((status, matched["commitment_id"]), (200, "TG-GOLD-H008"))
            self.assertEqual(lookup("What was Bally’s 70% Bronx hiring promise?", "jobs")[2]["outcome"], "AMBIGUOUS")

            original = call("GET", funding)[2]
            renewable = call("GET", energy)[2]
            self.assertIsNone(original["latest_completed_run"]["verification_date"])
            common_sources = {edge["source"]["id"]: edge for edge in original["source_edges"]}
            shared = [(edge, common_sources[edge["source"]["id"]]) for edge in renewable["source_edges"] if edge["source"]["id"] in common_sources]
            self.assertTrue(shared)
            self.assertTrue(any(a["evidence"][0]["status"] != b["evidence"][0]["status"] for a, b in shared))
            org_id = original["organization"]["id"]
            expanded = call("GET", f"/organizations/{org_id}/commitments")[2]
            self.assertIn("TG-GOLD-H010", {item["id"] for item in expanded["commitments"]})

            research_query = "Did Example Transit Authority promise a 2028 overnight electric shuttle pilot?"
            created = finish(lookup(research_query, "new"))
            self.assertEqual(created["status"], "COMPLETED", created)
            new_id = created["result"]["commitment_id"]
            graph = call("GET", f"/commitments/{new_id}")[2]
            self.assertEqual(graph["viewed_run_id"], created["result"]["run_id"])
            self.assertTrue(graph["commitment"]["original_promise"]["source"]["url"])
            self.assertEqual(lookup(research_query, "existing")[2]["commitment_id"], new_id)
            rejected = finish(lookup("An unrelated pledge with no public record", "unknown"))
            self.assertEqual(rejected["result"]["outcome"], "NO_PUBLIC_PROMISE")

            accepted = call("POST", funding + "/research-runs", "refresh")
            self.assertEqual(call("POST", funding + "/research-runs", "refresh"), accepted)
            new_run = finish(accepted)
            self.assertEqual(new_run["status"], "COMPLETED", new_run)
            self.assertEqual(new_run["result"]["commitment_id"], "TG-GOLD-H008")
            self.assertEqual(call("GET", funding)[2]["viewed_run_id"], new_run["result"]["run_id"])
            previous = call("GET", funding + "?run_id=" + original["viewed_run_id"])[2]
            self.assertEqual(previous["source_edges"], original["source_edges"])

            failed = finish(call("POST", energy + "/research-runs", "fail-refresh"))
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(call("GET", energy)[2]["viewed_run_id"], renewable["viewed_run_id"])

            restarted = PostgresStore(dsn)
            self.assertEqual(restarted.graph("TG-GOLD-H008")["viewed_run_id"], new_run["result"]["run_id"])
            report = export_golden(restarted, WORKBOOK)
            self.assertIn("TG-GOLD-A019", json.dumps(report))
