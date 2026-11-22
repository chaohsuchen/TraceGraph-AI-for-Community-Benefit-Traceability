"""Exercise the assembled evidence graph in a clean PostgreSQL catalog."""

import json
import os
from contextlib import ExitStack, nullcontext
from unittest import TestCase, skipUnless
from unittest.mock import patch

from tests.db_support import isolated_database
from tracegraph.runtime import create_application, prepare_catalog
from tracegraph.runtime_fixtures import ANNOUNCEMENT, AUDIT, BUDGET, FIXTURE_QUERY, ORIGINAL


@skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to a disposable PostgreSQL instance")
class FullIntegrationTest(TestCase):
    def test_refresh_saves_companion_and_assessment_from_the_same_evidence(self):
        from tracegraph.runtime_fixtures import fixture_judge, fixture_reconcile, fixture_research

        ordinary = fixture_research({"query": FIXTURE_QUERY}, None)
        quote = "The deployment register records one electric shuttle in overnight service during the pilot."
        url = "https://example.org/register-annex"
        page = "Deployment register, row 4. " + quote
        located = ([{"url": url, "quote": quote}], {url: "Deployment register annex"})
        seen = []

        def assess(context, state):
            excerpts = {s["excerpt"] for f in state["findings"] for s in f["supports"]}
            seen.append(excerpts)
            if quote in excerpts:
                return {"label": "PARTIALLY_CORROBORATED", "verdict": "One shuttle operated.", "reasoning": quote}
            return {"label": "UNVERIFIED", "verdict": "Delivery unverified.", "reasoning": "Only the plan is documented."}

        with isolated_database(os.environ["TEST_DATABASE_URL"]) as dsn, ExitStack() as stubs:
            prepare_catalog(dsn)
            stubs.enter_context(patch("tracegraph.runtime.read_credential", return_value="test-only"))
            stubs.enter_context(patch("tracegraph.providers.ClaudeIdentityJudge.__call__", side_effect=fixture_judge))
            stubs.enter_context(patch("tracegraph.openai_provider.OpenAIWebResearcher.__call__", return_value=ordinary))
            stubs.enter_context(patch("tracegraph.providers.ClaudeReconciliationJudge.__call__", side_effect=fixture_reconcile))
            stubs.enter_context(patch("tracegraph.providers.ClaudeReconciliationJudge.assess", side_effect=assess))
            search = stubs.enter_context(patch("tracegraph.openai_provider.OpenAIWebResearcher.locate_documents",
                                               side_effect=(located, located, TimeoutError("annex unavailable"))))
            fetch = stubs.enter_context(patch("tracegraph.openai_provider.load_public_text", return_value=page))
            extract = stubs.enter_context(patch("tracegraph.companion_documents._structured", return_value={
                "findings": [{"window_id": "w1", "quote": quote, "claim": quote}],
            }))
            app = create_application(dsn, "live", provider="openai")

            def run_job(path, key, payload=b""):
                status, _, job = app.respond("POST", "/api/v1" + path, {"Idempotency-Key": key}, payload)
                self.assertEqual(status, 202, job)
                self.assertTrue(app.process_one_job())
                finished = app.store.get_job(job["job_id"])
                self.assertEqual(finished["status"], "COMPLETED", finished)
                return finished["result"]["commitment_id"]

            cid = run_job("/lookup", "discover", json.dumps({"query": FIXTURE_QUERY}).encode())
            search.assert_not_called()
            path = f"/commitments/{cid}"

            def graph():
                status, _, value = app.respond("GET", "/api/v1" + path, {}, b"")
                self.assertEqual(status, 200)
                return value

            self.assertEqual(graph()["assessment"]["label"], "UNVERIFIED")
            for key in ("new-annex", "same-annex", "unavailable-annex"):
                with self.assertLogs("tracegraph.agent", level="WARNING") if key == "unavailable-annex" else nullcontext():
                    self.assertEqual(run_job(path + "/research-runs", key), cid)
                current = graph()
                self.assertEqual(current["assessment"]["label"], "PARTIALLY_CORROBORATED")
                self.assertEqual(current["assessment"]["reasoning"], quote)
                hits = [s for f in current["findings"] for s in f["supports"] if s["excerpt"] == quote]
                self.assertEqual([(s["source"]["url"], s["locator"]) for s in hits],
                                 [(url, f"Normalized source characters [{page.index(quote)}, {len(page)})")])
            self.assertEqual(seen, [{ORIGINAL, BUDGET}] + [{ORIGINAL, BUDGET, quote}] * 3)
            self.assertEqual(search.call_count, 3)
            fetch.assert_called_once()
            extract.assert_called_once()

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
                             "PARTIAL_RESEARCH")
            with psycopg.connect(dsn) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM commitment").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM research_run").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM workbook_annotation").fetchone()[0], 0)
