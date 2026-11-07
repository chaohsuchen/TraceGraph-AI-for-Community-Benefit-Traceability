"""Run the PostgreSQL-backed TraceGraph API and workbook commands."""

import argparse
import hashlib
import json
import logging
import os
import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

from tracegraph.agent import Agent, ResolutionUnavailable
from tracegraph.api import Application, make_handler
from tracegraph.retrieval import Retrieval


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "Golden Dataset (5).xlsx"
LOCAL_DSN = "postgresql://tracegraph:tracegraph@127.0.0.1:55432/tracegraph"
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
        row = connection.execute(
            "SELECT embedding_model, embedding_dimensions FROM tracegraph_catalog WHERE singleton"
        ).fetchone()
    if row != (embed.profile, embed.dimensions):
        raise ValueError("Database embedding profile is missing or incompatible. Prepare the database first.")


def create_application(dsn, mode, *, key_file=None, model=None):
    from tracegraph.postgres import PostgresStore
    from tracegraph.providers import LocalEmbedding

    embed = LocalEmbedding()
    check_embedding_profile(dsn, embed)
    if mode == "fixture":
        from tracegraph.runtime_fixtures import fixture_judge, fixture_research

        judge, researcher = fixture_judge, fixture_research
    else:
        from tracegraph.providers import AnthropicClient, ClaudeIdentityJudge, ClaudeWebResearcher

        client = AnthropicClient(api_key=read_credential(key_file), model=model)
        judge, researcher = ClaudeIdentityJudge(client), ClaudeWebResearcher(client)
    store = PostgresStore(dsn)
    return Application(store, Agent(Retrieval(store, embed, judge), store, researcher), (ResolutionUnavailable,))


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
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("migrate")
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--workbook", type=Path, default=WORKBOOK)
    export = actions.add_parser("export-golden")
    export.add_argument("--workbook", type=Path, default=WORKBOOK)
    export.add_argument("--output", type=Path, required=True)
    server = actions.add_parser("serve")
    server.add_argument("--mode", choices=("live", "fixture"), default="live")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    server.add_argument("--cors-origin")
    server.add_argument("--anthropic-key-file", type=Path)
    server.add_argument("--anthropic-model")
    args = parser.parse_args(argv)

    try:
        if args.action == "migrate":
            apply_migrations(args.database_url)
        elif args.action == "prepare":
            import psycopg

            from tracegraph.providers import LocalEmbedding
            from tracegraph.workbook_import import import_workbook

            apply_migrations(args.database_url)
            embed = LocalEmbedding()
            with psycopg.connect(args.database_url) as connection:
                result = import_workbook(
                    connection, args.workbook, embed,
                    embedding_model=embed.profile, embedding_dimensions=embed.dimensions,
                )
            print(json.dumps(result, ensure_ascii=False))
        elif args.action == "export-golden":
            from tracegraph.evaluation import export_golden
            from tracegraph.postgres import PostgresStore

            if args.output.resolve() == args.workbook.resolve():
                raise ValueError("Choose an output path different from the workbook.")
            payload = export_golden(PostgresStore(args.database_url), args.workbook)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            app = create_application(
                args.database_url, args.mode, key_file=args.anthropic_key_file, model=args.anthropic_model,
            )
            serve(app, args.host, args.port, args.cors_origin)
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
