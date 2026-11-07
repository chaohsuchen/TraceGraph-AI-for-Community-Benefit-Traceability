from datetime import date
from importlib.util import find_spec
from os import environ
from pathlib import Path
from unittest import TestCase, skipUnless
from uuid import uuid4

from tracegraph.evaluation import export_golden
from tracegraph.workbook_import import import_workbook


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "Golden Dataset (5).xlsx"
H008 = "TG-GOLD-H008"
H010 = "TG-GOLD-H010"
A019 = "TG-GOLD-A019"


@skipUnless(find_spec("openpyxl"), "Workbook import requires openpyxl.")
class WorkbookEvaluationTests(TestCase):
    def test_export_joins_current_prediction_and_keeps_both_gold_labels(self):
        class Store:
            def graph(self, cid):
                if cid == H008:
                    return {"latest_completed_run": {"verdict": "Current prediction"}}
                return None

        export = export_golden(Store(), WORKBOOK)
        records = {r["commitment_id"]: r for r in export["records"]}
        self.assertEqual(len(records), 100)
        self.assertEqual(records[H008]["prediction"]["latest_completed_run"]["verdict"],
                         "Current prediction")
        self.assertTrue(records[A019]["gold_label_conflict"])
        self.assertEqual(
            {row["fields"]["evidence_status"] for row in records[A019]["gold"]},
            {"not yet due", "source unavailable"},
        )
        self.assertIsNone(records[A019]["prediction"])
        self.assertEqual(export["issues"]["research_without_gold_row"], ["TG-GOLD-A015"])
        self.assertEqual(export["issues"]["sheet4_header_rows"], [1, 9])


@skipUnless(
    find_spec("openpyxl") and find_spec("psycopg") and environ.get("TEST_DATABASE_URL"),
    "Set TEST_DATABASE_URL to a PostgreSQL test database with pgvector.",
)
class WorkbookPostgresTests(TestCase):
    def test_import_is_atomic_and_idempotent_with_real_workbook(self):
        import psycopg
        from psycopg import sql

        schema = sql.Identifier("workbook_test_" + uuid4().hex)
        with psycopg.connect(environ["TEST_DATABASE_URL"], autocommit=True) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
            try:
                connection.execute(sql.SQL("SET search_path TO {}, public").format(schema))
                connection.execute((ROOT / "db" / "001_initial.sql").read_text(encoding="utf-8"))
                self._assert_import(connection)
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))

    def _assert_import(self, connection):
        def load(model):
            return import_workbook(connection, WORKBOOK, lambda _: [1, 0],
                                   embedding_model=model, embedding_dimensions=2)

        first = load("test-v1")
        self.assertEqual(
            [first[key] for key in ("staged_rows", "inserted_commitments", "inserted_historical_runs",
                                    "sources", "evidence")],
            [324, 100, 6, 24, 19],
        )
        self.assertEqual(connection.execute("""
            SELECT array_agg(attributes -> 'cells' ->> 'Q' ORDER BY row_number)
            FROM workbook_annotation WHERE sheet_name = 'gold commitments' AND gold_id = %s
        """, (A019,)).fetchone(), (["not yet due", "source unavailable"],))
        self.assertEqual(connection.execute("""
            SELECT count(*) FROM workbook_annotation
            WHERE sheet_name = 'commitment database' AND gold_id = 'TG-GOLD-A015'
        """).fetchone(), (1,))
        self.assertEqual(connection.execute("""
            SELECT count(*) FROM commitment WHERE id IN ('TG-GOLD-A015', 'TG-GOLD-A019')
        """).fetchone(), (1,))
        rows = connection.execute("""
            SELECT c.id, e.source_id, e.status, e.locator, e.retrieved_at,
                   s.published_on, r.verification_date, r.completed_at
            FROM commitment c
            JOIN research_run r ON r.commitment_id = c.id
            JOIN evidence e ON e.run_id = r.id AND e.source_id = c.original_source_id
            JOIN source s ON s.id = e.source_id
            WHERE c.id IN (%s, %s)
            ORDER BY c.id
        """, (H008, H010)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], rows[1][1])
        self.assertEqual(rows[0][2:], ("Commitment documented", "PDF p. 13", None,
                                      date(2025, 12, 1), None, None))
        self.assertEqual(rows[1][2:], ("Target documented", "PDF p. 14", None,
                                      date(2025, 12, 1), None, None))

        revision = connection.execute("SELECT revision FROM tracegraph_catalog").fetchone()
        repeat = load("test-v1")
        self.assertEqual((repeat["inserted_commitments"], repeat["inserted_historical_runs"]), (0, 0))
        with self.assertRaisesRegex(ValueError, "profile"):
            load("another-model")
        self.assertEqual(connection.execute("SELECT revision FROM tracegraph_catalog").fetchone(),
                         revision)
        self.assertEqual(connection.execute("SELECT count(*) FROM evidence").fetchone(), (19,))
        self.assertEqual(connection.execute("SELECT count(*) FROM workbook_annotation").fetchone(),
                         (324,))
