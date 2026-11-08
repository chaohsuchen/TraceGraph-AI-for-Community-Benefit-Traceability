"""Run TraceGraph and isolated evidence benchmarks."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from tracegraph.agent import Agent, ResolutionUnavailable
from tracegraph.api import Application, make_handler
from tracegraph.retrieval import Retrieval


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "Golden Dataset (5).xlsx"
LOCAL_DSN = "postgresql://tracegraph:tracegraph@127.0.0.1:55432/tracegraph"
LIVE_SCHEMA = "tracegraph_live"
LOG = logging.getLogger(__name__)


def apply_migrations(dsn, directory=ROOT / "db"):
    import psycopg

    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(43087411)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS tracegraph_schema_migration (
                name text PRIMARY KEY,
                sha256 text NOT NULL
            )
        """)
        applied = dict(connection.execute("SELECT name, sha256 FROM tracegraph_schema_migration"))
        for path in sorted(directory.glob("[0-9]*.sql")):
            script = path.read_text(encoding="utf-8-sig").strip()
            checksum = hashlib.sha256(script.encode()).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise ValueError(f"Applied migration {path.name} has changed")
                continue
            lines = script.splitlines()
            if lines[0].strip().upper() == "BEGIN;" and lines[-1].strip().upper() == "COMMIT;":
                script = "\n".join(lines[1:-1])
            connection.execute(script, prepare=False)
            connection.execute(
                "INSERT INTO tracegraph_schema_migration (name, sha256) VALUES (%s, %s)",
                (path.name, checksum),
            )


def scoped_database(dsn, schema, *, create=False):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    if not schema:
        return dsn
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError("Use a lowercase PostgreSQL schema name.")
    with psycopg.connect(dsn, autocommit=True) as connection:
        if create:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        elif not connection.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0]:
            raise ValueError("The application schema does not exist. Run prepare first.")
    options = conninfo_to_dict(dsn).get("options", "")
    return make_conninfo(dsn, options=f"{options} -c search_path={schema},public".strip())


def _require_system_catalog(connection):
    if connection.execute("SELECT EXISTS (SELECT 1 FROM workbook_annotation)").fetchone()[0]:
        raise ValueError("This schema contains imported workbook data. Use a clean application schema.")


def prepare_catalog(dsn):
    import psycopg
    from tracegraph.providers import LocalEmbedding

    apply_migrations(dsn)
    embed = LocalEmbedding()
    with psycopg.connect(dsn) as connection:
        _require_system_catalog(connection)
        connection.execute("""
            UPDATE tracegraph_catalog SET embedding_model = %s, embedding_dimensions = %s
            WHERE singleton AND embedding_model IS NULL
        """, (embed.profile, embed.dimensions))
        count = connection.execute("SELECT count(*) FROM commitment").fetchone()[0]
    check_embedding_profile(dsn, embed)
    return {"commitments": count, "embedding_profile": embed.profile}


@contextmanager
def evaluation_catalog(dsn):
    import psycopg
    from psycopg import sql

    schema = "tracegraph_eval_" + uuid4().hex
    scoped = scoped_database(dsn, schema, create=True)
    try:
        prepare_catalog(scoped)
        yield scoped
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def read_credential(path=None):
    if path is None:
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise ValueError("Set ANTHROPIC_API_KEY or pass --anthropic-key-file.")
        return key
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ValueError("Could not read the Anthropic credential file.") from None
    keys = set(re.findall(r"(?<![\w-])sk-ant-[A-Za-z0-9_-]{24,}(?![\w-])", raw))
    if len(keys) != 1:
        raise ValueError("The Anthropic credential file must contain exactly one key.")
    return keys.pop()


def check_embedding_profile(dsn, embed):
    import psycopg

    with psycopg.connect(dsn) as connection:
        _require_system_catalog(connection)
        row = connection.execute(
            "SELECT embedding_model, embedding_dimensions FROM tracegraph_catalog WHERE singleton"
        ).fetchone()
    if row != (embed.profile, embed.dimensions):
        raise ValueError("Database embedding profile is missing or incompatible. Prepare the database first.")


def create_application(dsn, mode, *, key_file=None, model=None):
    from tracegraph.evidence_store import EvidenceStore
    from tracegraph.providers import LocalEmbedding
    from tracegraph.reconcile import EvidenceReconciler

    embed = LocalEmbedding()
    check_embedding_profile(dsn, embed)
    if mode == "fixture":
        from tracegraph.runtime_fixtures import fixture_judge, fixture_reconcile, fixture_research, fixture_review

        judge, researcher = fixture_judge, fixture_research
        reconciler = EvidenceReconciler(fixture_reconcile, followup=fixture_review)
    else:
        from tracegraph.providers import AnthropicClient, ClaudeIdentityJudge, ClaudeReconciliationJudge, ClaudeWebResearcher

        client = AnthropicClient(api_key=read_credential(key_file), model=model)
        judge, researcher = ClaudeIdentityJudge(client), ClaudeWebResearcher(client)
        reconciliation_judge = ClaudeReconciliationJudge(client)
        reconciler = EvidenceReconciler(
            reconciliation_judge, followup=researcher.review_conflicts, assessor=reconciliation_judge.assess,
            verify_conflicts=reconciliation_judge.verify_conflicts,
            verify_refutation=reconciliation_judge.verify_refutation,
        )
    store = EvidenceStore(dsn)
    agent = Agent(Retrieval(store, embed, judge), store, researcher, reconciler=reconciler)
    return Application(store, agent, (ResolutionUnavailable,))


def evaluate(dsn, workbook, *, commitment_ids=None, reviewed_matches=None, mode="live", key_file=None, model=None):
    from tracegraph.benchmark import EvaluationCaseFailed, evaluate_workbook

    def research(query):
        with evaluation_catalog(dsn) as scoped:
            app = create_application(scoped, mode, key_file=key_file, model=model)
            status, _, body = app.respond("POST", "/api/v1/lookup", {"Idempotency-Key": "evaluation"},
                                          json.dumps({"query": query}).encode())
            if status != 202 or not app.process_one_job():
                raise EvaluationCaseFailed("The isolated evidence search did not start.")
            job = app.store.get_job(body["job_id"])
            if job["status"] != "COMPLETED":
                raise EvaluationCaseFailed("Evidence search failed: " + job.get("error", {}).get("code", "UNKNOWN"))
            result = job["result"]
            if result["outcome"] == "NO_PUBLIC_PROMISE":
                return {"draft": None}
            if result["outcome"] != "RESOLVED":
                raise EvaluationCaseFailed("Evidence search did not resolve the commitment.")
            return app.store.graph(result["commitment_id"])

    return evaluate_workbook(workbook, research, commitment_ids=commitment_ids, reviewed_matches=reviewed_matches)


def serve(app, host, port, cors_origin):
    stop = Event()

    def work():
        while not stop.is_set():
            try:
                if not app.process_one_job():
                    stop.wait(0.2)
            except Exception:
                LOG.exception("Could not process the research queue")
                stop.wait(1)

    worker = Thread(target=work, daemon=True)
    with ThreadingHTTPServer((host, port), make_handler(app, cors_origin)) as server:
        worker.start()
        print(f"TraceGraph API listening at http://{host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            worker.join(timeout=3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("TRACEGRAPH_DATABASE_URL", LOCAL_DSN))
    parser.add_argument("--schema", default=os.environ.get("TRACEGRAPH_SCHEMA", LIVE_SCHEMA))
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("migrate")
    actions.add_parser("prepare")
    benchmark = actions.add_parser("evaluate")
    benchmark.add_argument("--workbook", type=Path, default=WORKBOOK)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--commitment-id", action="append")
    benchmark.add_argument("--reviewed-matches", type=Path)
    benchmark.add_argument("--mode", choices=("live", "fixture"), default="live")
    benchmark.add_argument("--anthropic-key-file", type=Path)
    benchmark.add_argument("--anthropic-model")
    server = actions.add_parser("serve")
    server.add_argument("--mode", choices=("live", "fixture"), default="live")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    server.add_argument("--cors-origin")
    server.add_argument("--anthropic-key-file", type=Path)
    server.add_argument("--anthropic-model")
    args = parser.parse_args(argv)

    try:
        if args.action in {"migrate", "prepare"}:
            dsn = scoped_database(args.database_url, args.schema, create=True)
            if args.action == "migrate":
                apply_migrations(dsn)
            else:
                print(json.dumps(prepare_catalog(dsn), ensure_ascii=False))
        elif args.action == "evaluate":
            logging.basicConfig(level=logging.INFO)
            if args.output.resolve() == args.workbook.resolve():
                raise ValueError("Choose an output path different from the workbook.")
            reviews = json.loads(args.reviewed_matches.read_text(encoding="utf-8")) if args.reviewed_matches else None
            payload = evaluate(args.database_url, args.workbook, commitment_ids=args.commitment_id,
                               reviewed_matches=reviews, mode=args.mode, key_file=args.anthropic_key_file,
                               model=args.anthropic_model)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            logging.basicConfig(level=logging.INFO)
            dsn = scoped_database(args.database_url, args.schema)
            app = create_application(
                dsn, args.mode, key_file=args.anthropic_key_file, model=args.anthropic_model,
            )
            serve(app, args.host, args.port, args.cors_origin)
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
