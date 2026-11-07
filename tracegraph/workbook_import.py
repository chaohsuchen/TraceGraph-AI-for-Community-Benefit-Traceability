"""Import the reviewed Golden Dataset layout into a TraceGraph catalog."""

from collections import defaultdict
from datetime import date
from hashlib import sha256
from json import dumps
from math import isfinite
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from .workbook_stage import _json_value, extract_rows, issues


GOLD_SHEET = "gold commitments"
GOLD_FIELDS = {
    "B": ("document_id", "original_document_id"),
    "C": ("page_section", "original_locator"),
    "D": ("char_start", "original_char_start"),
    "E": ("char_end", "original_char_end"),
    "F": ("commitment_text", "original_text"),
    "G": ("normalized_statement", "description"),
    "H": ("obligation_type", "obligation_type"),
    "I": ("responsible_entity", "responsible_entity_raw"),
    "J": ("amount_value", "amount_value"),
    "K": ("term_periodicity", "term_periodicity"),
    "L": ("geographic_scope", "geographic_scope"),
    "M": ("beneficiary_category", "beneficiary_category"),
    "N": ("beneficiary_named", "beneficiary_named"),
    "O": ("condition_contingency", "condition_contingency"),
    "P": ("milestone", "milestone"),
    "U": ("notes", "notes"),
}
ORGANIZATIONS = {
    "Bally’s Corporation": "org-ballys",
    "Genting New York LLC d/b/a Resorts World New York City": "org-resorts-world-nyc",
    "Queens Future, LLC d/b/a Hard Rock Metropolitan Park": "org-hard-rock-metropolitan-park",
}
# The H-series tabs cite the Selection Report by name and date. The sources
# sheet does not establish a crosswalk for the other original document IDs.
ORIGINAL_DOCUMENT_URLS = {
    "GFLB-SEL-2025-12-01": (
        "https://nycasinos.ny.gov/system/files/documents/2025/12/12.01.25.gflbselectiondocument.pdf"
    ),
}
HYPERLINK = re.compile(r'=HYPERLINK\("([^"]+)",\s*"((?:[^"]|"")*)"\)', re.IGNORECASE)
CITATION_DATE = re.compile(r"^([A-Za-z]+)\.? (\d{1,2}), (\d{4})(?:,\s*|$)")
MONTHS = dict(zip(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
    range(1, 13),
))


def _cells(row):
    return row["attributes"]["cells"]


def gold_groups(rows):
    """Return the workbook header and each physical Golden row grouped by ID."""
    header = None
    groups = defaultdict(list)
    for row in rows:
        if row["sheet_name"] != GOLD_SHEET:
            continue
        if row["gold_id"]:
            groups[row["gold_id"]].append(row)
        elif _cells(row).get("A") == "gold_id":
            header = _cells(row)
    if header is None or any(header.get(col) != excel for col, (excel, _) in GOLD_FIELDS.items()):
        raise ValueError("Unexpected gold commitments header.")
    return header, groups


def _nullable_cell(value):
    if isinstance(value, str) and value.strip().casefold() in {"none stated", "none named", "none"}:
        return None
    return value


def _commitments(groups, titles):
    commitments = {}
    for gold_id, rows in groups.items():
        first = _cells(rows[0])
        values = {field: _nullable_cell(first.get(col)) for col, (_, field) in GOLD_FIELDS.items()}
        if any(
            _nullable_cell(_cells(row).get(col)) != values[field]
            for row in rows[1:] for col, (_, field) in GOLD_FIELDS.items()
        ):
            raise ValueError(f"Conflicting commitment data for {gold_id}.")
        if not values["description"]:
            raise ValueError(f"Missing normalized statement for {gold_id}.")
        for field in ("original_char_start", "original_char_end"):
            offset = values[field]
            if offset is not None:
                if isinstance(offset, bool) or int(offset) != offset or offset < 0:
                    raise ValueError(f"Invalid {field} for {gold_id}.")
                values[field] = int(offset)
        commitments[gold_id] = {
            **values,
            "title": titles.get(gold_id),
            "organization_id": ORGANIZATIONS.get(values["responsible_entity_raw"]),
        }
    return commitments


def _source_reference(row, column):
    value = _cells(row).get(column) or ""
    link = row["attributes"]["hyperlinks"].get(column)
    formula = HYPERLINK.fullmatch(value)
    if formula:
        target, value = formula.groups()
        link = link or target
        value = value.replace('""', '"')
    elif value.startswith(("https://", "http://")):
        link = link or value
        value = ""
    if not link:
        return None, value or None, None
    parts = urlsplit(link)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"Unsupported workbook source link {link!r}.")
    # Page fragments locate evidence within the same shared source.
    page = re.fullmatch(r"page=(\d+)", parts.fragment)
    if page:
        link = urlunsplit(parts._replace(fragment=""))
    return link, value or None, f"PDF p. {page[1]}" if page else None


def _split_citation(citation, page):
    match = CITATION_DATE.match(citation or "")
    if not match or match[1][:3].lower() not in MONTHS:
        return None, None, citation or page
    published_on = date(int(match[3]), MONTHS[match[1][:3].lower()], int(match[2]))
    date_text = citation[:match.end()].rstrip(", ")
    return date_text, published_on, citation[match.end():].strip() or page


def _source_id(url, title, citation):
    identity = url or f"{title}\n{citation}"
    return "workbook-source-" + sha256(identity.encode()).hexdigest()[:24]


def _reviewed_rows(rows):
    """Map the real source register and completed detail sheets."""
    sources, titles, runs, evidence = {}, {}, {}, []
    for row in rows:
        if row["sheet_name"] != "sources" or _cells(row).get("A") == "source_id":
            continue
        cells = _cells(row)
        url, citation, _ = _source_reference(row, "C")
        source_id = f"workbook-source-{cells['A']}"
        sources[url or source_id] = {
            "id": source_id, "title": cells["B"], "url": url, "citation": citation,
            "publisher": None, "source_type": cells.get("D"), "published_on": None,
        }
    details = defaultdict(list)
    for row in rows:
        if row["gold_id"] and row["sheet_name"] == row["gold_id"]:
            details[row["gold_id"]].append(row)
    for cid, detail in details.items():
        by_label = {_cells(row).get("A"): _cells(row).get("B") for row in detail}
        if by_label.get("Commitment") != cid:
            raise ValueError(f"Detail sheet identity differs from its worksheet name: {cid}.")
        by_row = {row["row_number"]: _cells(row) for row in detail}
        if by_row.get(2, {}).get("A"):
            titles[cid] = by_row[2]["A"]
        verdict, reasoning = by_label.get("Aggregated Evidence Status"), by_label.get("Reasoning")
        if not verdict or not reasoning:
            continue
        run_id = f"workbook-run-{cid}"
        runs[cid] = (run_id, verdict, reasoning)
        for row in detail:
            cells = _cells(row)
            if not cells.get("D") or cells.get("A") == "Document Name":
                continue
            url, reference, page = _source_reference(row, "B")
            title = cells["A"]
            if not (url or reference):
                raise ValueError(f"Missing citation on {cid} row {row['row_number']}.")
            citation, published_on, locator = _split_citation(reference, page)
            source_id = _source_id(url, title, reference)
            source_key = url or source_id
            source = sources.setdefault(source_key, {
                "id": source_id, "title": title, "url": url, "citation": None,
                "publisher": None, "source_type": None, "published_on": None,
            })
            for field, value in (
                ("citation", citation if url else reference),
                ("publisher", cells.get("C")),
                ("published_on", published_on),
            ):
                if source[field] is None:
                    source[field] = value
                elif value is not None and source[field] != value:
                    raise ValueError(f"Conflicting {field} for workbook source {source_key}.")
            evidence.append({
                "id": f"workbook-evidence-{cid}-{row['row_number']}", "run_id": run_id,
                "source_key": source_key, "status": cells["D"], "summary": cells.get("E"),
                "explanation": cells.get("F"), "locator": locator,
            })
    return sources, titles, runs, evidence


def _vector(embedding, dimensions):
    values = [float(value) for value in embedding]
    if len(values) != dimensions or not all(map(isfinite, values)) or not any(values):
        raise ValueError("Each workbook embedding must be finite, nonzero and match the model dimensions.")
    return dumps(values, allow_nan=False)


def _lock_catalog(cursor, model, dimensions):
    cursor.execute("""
        SELECT embedding_model, embedding_dimensions
        FROM tracegraph_catalog WHERE singleton FOR UPDATE
    """)
    profile = cursor.fetchone()
    if profile == (None, None):
        cursor.execute("""
            UPDATE tracegraph_catalog SET embedding_model = %s, embedding_dimensions = %s
            WHERE singleton
        """, (model, dimensions))
    elif profile != (model, dimensions):
        raise ValueError("Workbook embedding profile does not match the existing catalog.")


def _write_sources(cursor, sources):
    result = {}
    fields = ("id", "title", "url", "citation", "publisher", "source_type", "published_on")
    for key, source in sources.items():
        cursor.execute("""
            INSERT INTO source (id, title, url, citation, publisher, source_type, published_on)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id
        """, tuple(source[field] for field in fields))
        inserted = cursor.fetchone()
        if not inserted:
            if source["url"]:
                cursor.execute("SELECT id FROM source WHERE url = %s", (source["url"],))
            else:
                cursor.execute("""
                    SELECT id FROM source WHERE id = %s AND title = %s AND citation = %s
                """, (source["id"], source["title"], source["citation"]))
            inserted = cursor.fetchone()
        if not inserted:
            raise ValueError(f"Workbook source ID conflicts with an existing source: {source['id']}.")
        result[key] = inserted[0]
    return result


def _write_commitments(cursor, commitments, source_ids, embeddings):
    cursor.execute("SELECT id FROM commitment WHERE id = ANY(%s)", (list(commitments),))
    existing = {row[0] for row in cursor.fetchall()}
    fields = (
        "organization_id", "title", "description", "original_text", "responsible_entity_raw",
        "beneficiary_category", "beneficiary_named", "obligation_type", "amount_value",
        "term_periodicity", "geographic_scope", "condition_contingency", "milestone", "notes",
        "original_document_id", "original_locator", "original_char_start", "original_char_end",
    )
    cursor.executemany("""
        INSERT INTO commitment (
            id, organization_id, title, description, original_text, responsible_entity_raw,
            beneficiary_category, beneficiary_named, obligation_type, amount_value,
            term_periodicity, geographic_scope, condition_contingency, milestone, notes,
            original_document_id, original_locator, original_char_start, original_char_end,
            original_source_id, embedding
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector
        )
    """, [
        (cid, *(item[field] for field in fields),
         source_ids.get(ORIGINAL_DOCUMENT_URLS.get(item["original_document_id"])), embeddings[cid])
        for cid, item in commitments.items() if cid not in existing
    ])
    return len(commitments) - len(existing)


def import_workbook(connection, workbook, embed_commitment, *, embedding_model, embedding_dimensions):
    """Stage original rows and atomically insert the reviewed catalog records.

    Existing canonical commitments and workbook runs remain intact on re-import.
    Use an idle psycopg connection. Embeddings are generated before taking the lock.
    """
    from psycopg.pq import TransactionStatus
    from psycopg.rows import tuple_row

    if not embedding_model.strip() or type(embedding_dimensions) is not int or embedding_dimensions < 1:
        raise ValueError("Provide the embedding model ID and a positive integer dimension.")
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError("Workbook import requires an idle database connection.")

    rows = list(extract_rows(Path(workbook)))
    _, groups = gold_groups(rows)
    sources, titles, runs, evidence = _reviewed_rows(rows)
    commitments = _commitments(groups, titles)
    if runs.keys() - commitments.keys():
        raise ValueError("A completed detail sheet is missing its Golden commitment.")
    embeddings = {
        cid: _vector(embed_commitment(item["description"]), embedding_dimensions)
        for cid, item in commitments.items()
    }

    with connection.transaction(), connection.cursor(row_factory=tuple_row) as cursor:
        _lock_catalog(cursor, embedding_model, embedding_dimensions)
        cursor.executemany("""
            INSERT INTO workbook_annotation (workbook_id, sheet_name, row_number, gold_id, attributes)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT DO NOTHING
        """, [
            (r["workbook_id"], r["sheet_name"], r["row_number"], r["gold_id"],
             dumps(r["attributes"], ensure_ascii=False, default=_json_value))
            for r in rows
        ])
        cursor.executemany("""
            INSERT INTO organization (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING
        """, [(org_id, name) for name, org_id in ORGANIZATIONS.items()])
        source_ids = _write_sources(cursor, sources)
        inserted_count = _write_commitments(cursor, commitments, source_ids, embeddings)
        inserted_runs = set()
        for cid, (run_id, verdict, reasoning) in runs.items():
            cursor.execute("""
                INSERT INTO research_run (id, commitment_id, verdict, reasoning)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                RETURNING id
            """, (run_id, cid, verdict, reasoning))
            if cursor.fetchone():
                inserted_runs.add(run_id)
        cursor.executemany("""
            INSERT INTO evidence (id, run_id, source_id, status, explanation, summary, locator)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, [
            (item["id"], item["run_id"], source_ids[item["source_key"]], item["status"],
             item["explanation"], item["summary"], item["locator"])
            for item in evidence if item["run_id"] in inserted_runs
        ])

    return {
        **issues(rows), "workbook_id": rows[0]["workbook_id"], "staged_rows": len(rows),
        "commitments": len(commitments), "inserted_commitments": inserted_count,
        "historical_runs": len(runs), "inserted_historical_runs": len(inserted_runs),
        "sources": len(sources), "evidence": len(evidence),
    }
