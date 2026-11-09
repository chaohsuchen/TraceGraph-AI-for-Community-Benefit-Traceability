from importlib.util import find_spec
from unittest import TestCase, skipUnless

from tracegraph.runtime import WORKBOOK
from tracegraph.workbook_stage import extract_rows, issues


@skipUnless(find_spec("openpyxl"), "Workbook staging requires openpyxl")
class WorkbookStageTests(TestCase):
    def test_real_workbook_preserves_conflicts_and_links(self):
        rows = list(extract_rows(WORKBOOK))
        report = issues(rows)
        self.assertEqual((report["gold_rows"], report["unique_gold_ids"]), (101, 100))
        self.assertEqual(report["research_without_gold_row"], ["TG-GOLD-A015"])
        self.assertEqual(report["sheet4_header_rows"], [1, 9])
        duplicates = report["repeated_gold_ids"]["TG-GOLD-A019"]
        self.assertEqual({entry["evidence_status"] for entry in duplicates},
                         {"not yet due", "source unavailable"})
        h008 = next(row for row in rows if row["sheet_name"] == "TG-GOLD-H008"
                    and row["row_number"] == 5)
        self.assertEqual(h008["gold_id"], "TG-GOLD-H008")
        self.assertTrue(h008["attributes"]["hyperlinks"]["B"].startswith("https://"))
