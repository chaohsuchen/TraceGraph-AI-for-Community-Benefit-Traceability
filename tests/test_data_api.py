from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from json import dumps, loads
from threading import Thread
from unittest import TestCase

from tracegraph.api import PREFIX, Application, demo_application, make_handler
from tracegraph.data import StaleClaim


class ApiTests(TestCase):
    def setUp(self):
        self.app = demo_application()
        self.h008 = f"{PREFIX}/commitments/TG-GOLD-H008"
        self.h001 = f"{PREFIX}/commitments/TG-GOLD-H001"

    def get(self, path):
        return self.app.respond("GET", path, {})

    def post(self, path, key, **body):
        return self.app.respond(
            "POST", path, {"idempotency-key": key}, dumps(body).encode() if body else b"",
        )

    def test_lookup_results_and_replay(self):
        queries = self.app.agent.lookup_examples
        for query, example in queries.items():
            with self.subTest(query=query):
                status, headers, body = self.post(f"{PREFIX}/lookup", query, query=query)
                self.assertEqual(status, example["status"])
                if status == 202:
                    self.assertEqual(headers["Location"], f"{PREFIX}/research-jobs/{body['job_id']}")
                    self.assertEqual(self.get(headers["Location"])[2]["status"], "QUEUED")
                    self.assertTrue(self.app.process_one_job())
                    outcome = self.get(headers["Location"])[2]["result"]["outcome"]
                    self.assertEqual(outcome, "NO_PUBLIC_PROMISE")
                else:
                    self.assertEqual(body, example["body"])
        self.assertEqual(self.post(f"{PREFIX}/lookup", "reuse", query="one")[0], 202)
        status, _, body = self.post(f"{PREFIX}/lookup", "reuse", query="two")
        self.assertEqual((status, body["error"]["code"]), (409, "IDEMPOTENCY_CONFLICT"))
        self.assertEqual(self.post(f"{PREFIX}/lookup", "blank", query="  ")[0], 400)

    def test_demo_renewable_query_matches_h010(self):
        result = self.post(f"{PREFIX}/lookup", "renewable", query="What did Bally’s say about renewable electricity?")
        self.assertEqual(result, (200, {}, {"outcome": "MATCH", "commitment_id": "TG-GOLD-H010"}))

    def test_invalid_resolver_id_reports_unavailable(self):
        class ResolutionUnavailable(RuntimeError):
            pass

        class BrokenAgent:
            def lookup(self, _query):
                raise ResolutionUnavailable("Resolver selected a commitment outside its candidate list.")

        self.app = Application(self.app.store, BrokenAgent(), (ResolutionUnavailable,))
        status, _, body = self.post(f"{PREFIX}/lookup", "invalid-id", query="Query with an invalid result")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "RESOLUTION_UNAVAILABLE")
        self.assertFalse(self.app.process_one_job())

    def test_rejects_overlong_query_and_idempotency_keys(self):
        lookup = f"{PREFIX}/lookup"
        refresh = self.h008 + "/research-runs"
        self.assertEqual(self.post(lookup, "valid-key", query="x" * 4001)[0], 400)
        self.assertEqual(self.post(lookup, "x" * 129, query="valid query")[0], 400)
        self.assertEqual(self.post(refresh, "x" * 129)[0], 400)
        self.assertFalse(self.app.process_one_job())
        self.assertEqual(self.post(lookup, "x" * 128, query="x" * 4000)[0], 202)
        self.assertEqual(self.post(refresh, "x" * 128)[0], 202)

    def test_refresh_replay_and_history(self):
        target = self.h008 + "/research-runs"
        initial = self.get(self.h008)[2]
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.post(target, "key"), range(8)))
        self.assertTrue(all(response == responses[0] for response in responses))
        status, headers, _ = responses[0]
        self.assertEqual(status, 202)
        self.assertTrue(self.app.process_one_job())
        self.assertFalse(self.app.process_one_job())
        job = self.get(headers["Location"])[2]
        self.assertEqual(job["result"]["commitment_id"], "TG-GOLD-H008")
        self.assertEqual(job["status"], "COMPLETED")

        previous_id = initial["viewed_run_id"]
        history = self.get(f"{self.h008}?run_id={previous_id}")[2]
        self.assertEqual(history["latest_completed_run"]["id"], job["result"]["run_id"])
        self.assertEqual(history["source_edges"], initial["source_edges"])
        self.assertEqual(history["viewed_run_id"], previous_id)
        self.assertEqual(self.get(f"{self.h001}?run_id={previous_id}")[0], 404)

    def test_failure_does_not_create_a_run(self):
        _, headers, _ = self.post(self.h001 + "/research-runs", "failure")
        self.assertTrue(self.app.process_one_job())
        self.assertEqual(self.get(headers["Location"])[2]["status"], "FAILED")
        graph = self.get(self.h001)[2]
        self.assertIsNone(graph["latest_completed_run"])
        self.assertEqual(graph["source_edges"], [])

    def test_graph_sources_and_organization(self):
        one = self.get(self.h008)[2]
        other = self.get(f"{PREFIX}/commitments/TG-GOLD-H010")[2]
        self.assertEqual(one["source_edges"][0]["source"]["id"], other["source_edges"][0]["source"]["id"])
        self.assertNotEqual(one["source_edges"][0]["evidence"], other["source_edges"][0]["evidence"])
        group = self.get(f"{PREFIX}/organizations/fixture-org-ballys/commitments")[2]
        self.assertIn("TG-GOLD-H009", [row["id"] for row in group["commitments"]])

    def test_real_http_respects_response_headers(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, "http://localhost:5173")) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("POST", self.h008 + "/research-runs", headers={
                    "Idempotency-Key": "network", "Origin": "http://localhost:5173",
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 202)
                job_id = loads(response.read())["job_id"]
                self.assertEqual(response.getheader("Location"), f"{PREFIX}/research-jobs/{job_id}")
                self.assertEqual(response.getheader("Access-Control-Expose-Headers"), "Location")
            finally:
                connection.close()
                server.shutdown()
                thread.join()


class StorageTests(TestCase):
    def test_discovery_requires_current_revision_and_a_current_claim(self):
        store = demo_application().store
        revision = store.with_snapshot(lambda snapshot: snapshot.catalog_revision)
        store.enqueue("DISCOVER", query="new")
        store.enqueue("DISCOVER", query="second")
        first, second = store.claim_next(), store.claim_next()
        draft = {
            "description": "Synthetic source-backed commitment",
            "embedding": [1.0, 0.0],
            "original_promise": {
                "source": {"title": "Example promise", "url": "https://example.org/promise"},
                "locator": "Section 2", "excerpt": "We will provide the pilot.",
            },
        }
        research = {
            "verification_date": "2026-11-07", "verdict": "Documented",
            "reasoning": "The source documents the original promise.", "evidence": [],
        }
        committed = store.finish_discovery(first, revision, draft, research)
        cid = committed["commitment_id"]
        self.assertEqual(store.finish_discovery(second, revision, draft, research), {
            "outcome": "RECHECK_REQUIRED",
        })
        with self.assertRaises(StaleClaim):
            store.finish_discovery(first, revision, draft, research)
        found = store.with_snapshot(lambda snapshot: snapshot.vector_top_k([1.0, 0.0], 1))
        self.assertEqual(found, [{"commitment_id": cid, "rank": 1}])
        self.assertEqual(store.load_commitment(cid)["commitment"]["id"], cid)
