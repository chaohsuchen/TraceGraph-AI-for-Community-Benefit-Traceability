"""Stage workbook rows without resolving uncertain annotations or assigning verdicts."""

import argparse
import csv
from collections import defaultdict
from datetime import date
from hashlib import file_digest
from json import dumps
from pathlib import Path
import sys


FIELDS = ("workbook_id", "sheet_name", "row_number", "gold_id", "attributes")


def extract_rows(path):
    from openpyxl import load_workbook

    with path.open("rb") as handle:
        workbook_id = file_digest(handle, "sha256").hexdigest()
    workbook = load_workbook(path)
    try:
        for sheet in workbook:
            sheet_id = sheet.title if sheet.title.startswith("TG-GOLD-") else None
            for row in sheet.iter_rows():
                cells = {cell.column_letter: cell.value for cell in row if cell.value is not None}
                hyperlinks = {
                    cell.column_letter: cell.hyperlink.target or cell.hyperlink.location
                    for cell in row if cell.hyperlink
                }
                if not (cells or hyperlinks):
                    continue
                first = cells.get("A")
                gold_id = first if isinstance(first, str) and first.startswith("TG-GOLD-") else sheet_id
                yield {
                    "workbook_id": workbook_id,
                    "sheet_name": sheet.title,
                    "row_number": row[0].row,
                    "gold_id": gold_id,
                    "attributes": {"cells": cells, "hyperlinks": hyperlinks},
                }
    finally:
        workbook.close()


def issues(rows):
    gold = defaultdict(list)
    legacy_ids = set()
    sheet4_headers = []
    for row in rows:
        sheet, gold_id = row["sheet_name"], row["gold_id"]
        cells = row["attributes"]["cells"]
        if sheet == "gold commitments" and gold_id:
            gold[gold_id].append({"row": row["row_number"], "evidence_status": cells.get("Q")})
        elif sheet == "commitment database" and gold_id:
            legacy_ids.add(gold_id)
        elif sheet == "Sheet4" and cells.get("A") == "gold_id":
            sheet4_headers.append(row["row_number"])
    return {
        "gold_rows": sum(map(len, gold.values())),
        "unique_gold_ids": len(gold),
        "repeated_gold_ids": {key: entries for key, entries in gold.items() if len(entries) > 1},
        "research_without_gold_row": sorted(legacy_ids - gold.keys()),
        "sheet4_header_rows": sheet4_headers,
    }


def _json_value(value):
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Unsupported workbook cell type {type(value).__name__}")


def main():
    parser = argparse.ArgumentParser(description="Export original workbook rows as UTF-8 staging CSV.")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.workbook.resolve() == args.output.resolve():
        parser.error("Use a separate CSV output path.")

    rows = list(extract_rows(args.workbook))
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            attributes = dumps(row["attributes"], ensure_ascii=False, default=_json_value)
            writer.writerow({**row, "attributes": attributes})
    print(dumps(issues(rows), indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
