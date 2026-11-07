"""Isolate an integration test in a disposable PostgreSQL schema."""

from contextlib import contextmanager
from uuid import uuid4


@contextmanager
def isolated_database(dsn):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    schema = "tracegraph_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        options = conninfo_to_dict(dsn).get("options", "")
        scoped = make_conninfo(dsn, options=f"{options} -c search_path={schema},public".strip())
        try:
            yield scoped
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
