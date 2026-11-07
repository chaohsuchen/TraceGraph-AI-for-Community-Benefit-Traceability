"""Join Golden workbook labels with current API graph projections by commitment ID."""

from json import dumps, loads
from pathlib import Path

from .workbook_import import gold_groups
from .workbook_stage import _json_value, extract_rows, issues


def export_golden(store, workbook):
    """Return JSON-ready evaluation rows with every original label and current prediction.

    Workbook evidence_status and the run's free-text verdict are distinct fields.
    This export preserves both and leaves taxonomy mapping and scoring to evaluators.
    """
    rows = list(extract_rows(Path(workbook)))
    header, groups = gold_groups(rows)
    records = []
    for cid, group in sorted(groups.items()):
        gold = [
            {
                "row_number": row["row_number"],
                "fields": {
                    name: row["attributes"]["cells"].get(column) for column, name in header.items()
                },
            }
            for row in group
        ]
        labels = {row["fields"]["evidence_status"] for row in gold}
        records.append({
            "commitment_id": cid,
            "gold": gold,
            "gold_label_conflict": len(labels) > 1,
            "prediction": store.graph(cid),
        })
    result = {
        "workbook_id": rows[0]["workbook_id"],
        "gold_sheet": "gold commitments",
        "issues": issues(rows),
        "records": records,
    }
    return loads(dumps(result, ensure_ascii=False, default=_json_value))
