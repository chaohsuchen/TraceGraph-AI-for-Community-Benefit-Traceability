"""Dependency-free HTTP bindings for TraceGraph's MVP contract."""

from argparse import ArgumentParser
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import JSONDecodeError, dumps, loads
from logging import getLogger
from pathlib import Path
from threading import Event, Thread
from urllib.parse import parse_qs, quote, unquote, urlsplit

from tracegraph.data import IdempotencyConflict, MemoryStore, StaleClaim


PREFIX = "/api/v1"
LOG = getLogger(__name__)


def error(code, message, retryable=False):
    return {"code": code, "message": message, "retryable": retryable}


def response_error(status, code, message, retryable=False):
    return status, {}, {"error": error(code, message, retryable)}


def accepted(job_id, suggestions=None):
    body = {"job_id": job_id}
    if suggestions:
        body["suggestions"] = suggestions
    return 202, {"Location": f"{PREFIX}/research-jobs/{quote(job_id, safe='')}"}, body


class Application:
    def __init__(self, store, agent, resolution_errors=()):
        self.store = store
        self.agent = agent
        self.resolution_errors = resolution_errors

    def respond(self, method, target, headers, body=b""):
        try:
            return self._route(method, target, headers, body)
        except IdempotencyConflict:
            return response_error(409, "IDEMPOTENCY_CONFLICT", "This key was already used for a different request.")
        except Exception:
            LOG.exception("Request failed")
            return response_error(500, "INTERNAL_ERROR", "The request could not be completed.", True)

    def _route(self, method, target, headers, body):
        url = urlsplit(target)
        if not url.path.startswith(PREFIX + "/"):
            return self._missing()
        path = [unquote(segment) for segment in url.path[len(PREFIX) + 1:].split("/")]
        match method, path:
            case "POST", ["lookup"]:
                return self._lookup(headers, body)
            case "POST", ["commitments", commitment_id, "research-runs"]:
                return self._refresh(commitment_id, headers, body)
            case "GET", ["commitments", commitment_id]:
                parameters = parse_qs(url.query, keep_blank_values=True)
                runs = parameters.get("run_id", [])
                if len(runs) > 1 or runs == [""]:
                    return self._invalid("Specify one nonempty run_id.")
                return self._read(self.store.graph(commitment_id, runs[0] if runs else None))
            case "GET", ["organizations", organization_id, "commitments"]:
                return self._read(self.store.organization_commitments(organization_id))
            case "GET", ["research-jobs", job_id]:
                return self._read(self.store.get_job(job_id))
            case _:
                return self._missing()

    def _lookup(self, headers, body):
        key = self._key(headers)
        if not key:
            return self._invalid("Idempotency-Key must contain 1 to 128 characters.")
        try:
            request = loads(body)
        except (JSONDecodeError, UnicodeDecodeError):
            return self._invalid("Submit a JSON object with a query.")
        if (not isinstance(request, dict) or set(request) != {"query"}
                or not isinstance(request["query"], str) or not request["query"].strip()
                or len(request["query"]) > 4000):
            return self._invalid("Submit a JSON object with a nonempty query of at most 4000 characters.")
        query = request["query"].strip()
        return self.store.submit("lookup", key, {"query": query}, lambda: self._resolve(query))

    def _resolve(self, query):
        try:
            outcome = self.agent.lookup(query)
        except self.resolution_errors:
            return response_error(503, "RESOLUTION_UNAVAILABLE", "The identity check could not be completed.", True)
        if outcome["outcome"] == "START_DISCOVERY":
            return accepted(self.store.enqueue("DISCOVER", query=outcome["query"]), outcome.get("suggestions"))
        if outcome["outcome"] in ("MATCH", "AMBIGUOUS"):
            return 200, {}, outcome
        raise ValueError("Agent returned an unsupported lookup outcome.")

    def _refresh(self, cid, headers, body):
        key = self._key(headers)
        if not key:
            return self._invalid("Idempotency-Key must contain 1 to 128 characters.")
        if body:
            return self._invalid("Refresh accepts no request body.")
        if self.store.load_commitment(cid) is None:
            return self._missing()
        return self.store.submit(
            ("refresh", cid), key, {}, lambda: accepted(self.store.enqueue("REFRESH", commitment_id=cid)),
        )

    @staticmethod
    def _key(headers):
        key = next((value for name, value in headers.items()
                    if name.lower() == "idempotency-key"), "")
        return key.strip() if len(key) <= 128 else ""

    @staticmethod
    def _invalid(message):
        return response_error(400, "INVALID_REQUEST", message)

    @staticmethod
    def _missing():
        return response_error(404, "NOT_FOUND", "This resource was not found.")

    def _read(self, item):
        return self._missing() if item is None else (200, {}, item)

    def process_one_job(self):
        claim = self.store.claim_next()
        if claim is None:
            return False
        try:
            keep_alive = getattr(self.store, 'keep_claim_alive', nullcontext)
            with keep_alive(claim):
                self.agent.execute(claim)
        except StaleClaim:
            LOG.info("The job is no longer assigned to this worker.")
        except Exception:
            LOG.exception("Research job failed")
            try:
                self.store.fail(claim, error("INTERNAL_ERROR", "The research job failed.", True))
            except StaleClaim:
                LOG.info("The failed job is no longer assigned to this worker.")
        return True


class FixtureAgent:
    def __init__(self, store, lookup_examples):
        self.store = store
        self.lookup_examples = lookup_examples

    def lookup(self, query):
        response = self.lookup_examples.get(query)
        if response and response["status"] == 200:
            return response["body"]
        if "renewable electricity" in query.casefold():
            return {"outcome": "MATCH", "commitment_id": "TG-GOLD-H010"}
        return {"outcome": "START_DISCOVERY", "query": query}

    def execute(self, claim):
        if claim["kind"] == "DISCOVER":
            self.store.finish_without_run(claim, {
                "outcome": "NO_PUBLIC_PROMISE",
                "message": "The fixture catalog has no original promise source for this query.",
            })
            return
        graph = self.store.graph(claim["commitment_id"])
        previous = graph["latest_completed_run"]
        if previous is None:
            self.store.fail(claim, error(
                "RESEARCH_UNAVAILABLE", "This fixture has no evidence for a simulated refresh.", True,
            ))
            return
        evidence = [
            {**item, "source": {**edge["source"], "source_id": edge["source"]["id"]},
             "retrieved_at": "2026-11-07T12:00:00Z"}
            for edge in graph["source_edges"] for item in edge["evidence"]
        ]
        self.store.finish_refresh(claim, {
            "verification_date": "2026-11-07",
            "verdict": previous["verdict"],
            "reasoning": "Simulated refresh of the existing contract fixture.",
            "evidence": evidence,
        })


def demo_application():
    path = Path(__file__).resolve().parents[1] / "contracts" / "examples.json"
    examples = loads(path.read_text(encoding="utf-8"))
    graphs, groups, lookups = [], [], {}
    for item in examples:
        request, response = item["request"], item["response"]
        path = request["path"]
        if path.startswith(f"{PREFIX}/commitments/") and request["method"] == "GET" and not request.get("query"):
            graphs.append(response["body"])
        elif path.startswith(f"{PREFIX}/organizations/"):
            groups.append(response["body"])
        elif path == f"{PREFIX}/lookup" and response["status"] < 400:
            lookups[request["body"]["query"]] = response
    store = MemoryStore(graphs, groups)
    return Application(store, FixtureAgent(store, lookups))


def make_handler(app, allowed_origin):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            if allowed_origin and self.headers.get("Origin") == allowed_origin:
                self.send_header("Access-Control-Allow-Origin", allowed_origin)
                self.send_header("Access-Control-Expose-Headers", "Location")
                self.send_header("Vary", "Origin")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Idempotency-Key")
            self.end_headers()

        def _serve(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            if not 0 <= size <= 1_048_576:
                result = app._invalid("Invalid request body size.")
            else:
                result = app.respond(self.command, self.path, self.headers, self.rfile.read(size))
            status, headers, body = result
            payload = dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _serve

    return Handler


def main():
    parser = ArgumentParser(description="Serve the TraceGraph API against local fixtures.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cors-origin", default="http://localhost:5173")
    args = parser.parse_args()
    app = demo_application()
    stopped = Event()

    def work():
        while not stopped.is_set():
            if not app.process_one_job():
                stopped.wait(0.1)

    worker = Thread(target=work, daemon=True)
    with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app, args.cors_origin)) as server:
        worker.start()
        print(f"TraceGraph fixture API listening at http://127.0.0.1:{server.server_port}{PREFIX}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            worker.join()


if __name__ == "__main__":
    main()
