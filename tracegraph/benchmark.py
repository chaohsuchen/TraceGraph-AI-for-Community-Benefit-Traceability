"""Score independent research against the authored workbook detail tabs."""

from collections import Counter, defaultdict
from pathlib import Path
from unicodedata import normalize
from urllib.parse import urlsplit, urlunsplit

from .workbook_import import _reviewed_rows, gold_groups
from .workbook_stage import extract_rows


def evaluate_workbook(workbook, research, *, commitment_ids=None, reviewed_matches=None):
    """Call research(query) once per selected commitment and score its evidence.

    By default, use the first two completed detail tabs by ID. Review overrides
    pair an Excel row number with a one-based returned evidence index, grouped
    by commitment ID. The callback receives only the normalized commitment text.
    """
    reference_cases = _reference_cases(workbook)
    if isinstance(commitment_ids, str):
        raise ValueError("commitment_ids must be a sequence of commitment IDs")
    selected = sorted(reference_cases)[:2] if commitment_ids is None else list(commitment_ids)
    if not selected or len(selected) != len(set(selected)) or set(selected) - reference_cases.keys():
        raise ValueError("Select distinct commitment IDs with authored detail tabs")
    reviewed = reviewed_matches or {}
    if not isinstance(reviewed, dict) or set(reviewed) - set(selected):
        raise ValueError("Reviewed matches must belong to the selected commitments")

    cases = []
    totals = Counter()
    for commitment_id in selected:
        query, references = reference_cases[commitment_id]
        returned = _returned_evidence(research(query))
        score = _score(references, returned, reviewed.get(commitment_id, []))
        totals.update(score["counts"])
        cases.append({"commitment_id": commitment_id, **score})

    return {"cases": cases, "counts": dict(totals), "recall": totals["matched"] / totals["reference"]}


def _reference_cases(workbook):
    rows = list(extract_rows(Path(workbook)))
    _, groups = gold_groups(rows)
    sources, _, runs, evidence = _reviewed_rows(rows)
    by_run = defaultdict(dict)
    for item in evidence:
        row_number = int(item["id"].rsplit("-", 1)[-1])
        by_run[item["run_id"]][row_number] = {**item, "source": sources[item["source_key"]]}

    cases = {}
    for commitment_id, (run_id, _, _) in runs.items():
        if run_id not in by_run:
            continue
        queries = {row["attributes"]["cells"].get("G") for row in groups.get(commitment_id, ())}
        if len(queries) != 1 or not _text(next(iter(queries))):
            raise ValueError(f"Expected one normalized commitment description for {commitment_id}")
        cases[commitment_id] = (queries.pop().strip(), by_run[run_id])
    return cases


def _returned_evidence(response):
    if not isinstance(response, dict):
        raise ValueError("Research must return a result object")
    if response == {"draft": None}:
        return []
    result = response.get("result", response)
    if not isinstance(result, dict):
        raise ValueError("Research must return a result object")
    if "findings" in result:
        findings = result["findings"]
        if not isinstance(findings, list):
            raise ValueError("Research findings must be an array")
        evidence = []
        for finding in findings:
            supports = finding.get("supports") if isinstance(finding, dict) else None
            if not isinstance(supports, list):
                raise ValueError("Each finding must include a supports array")
            evidence.extend(supports)
    else:
        evidence = result.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("Research must return an evidence array or findings with supports")
    if any(not isinstance(item, dict) for item in evidence):
        raise ValueError("Each returned evidence item must be an object")
    return evidence


def _text(value):
    return " ".join(normalize("NFC", value).casefold().split()) if isinstance(value, str) else ""


def _url(value):
    if not isinstance(value, str):
        return ""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    fragment = parts.fragment
    if fragment.startswith("page=") and fragment[5:].isdigit():
        fragment = ""
    return urlunsplit(parts._replace(netloc=parts.netloc.lower(), fragment=fragment))


def _evidence_key(item):
    source = item.get("source")
    if not isinstance(source, dict):
        source = {}
    texts = {_text(item.get(field)) for field in ("excerpt", "summary")} - {""}
    locator = _text(item.get("locator"))
    if locator in {"cited passage", "whole document", "full text"}:
        locator = ""
    return _url(source.get("url")), _text(source.get("title")), _text(source.get("citation")), locator, texts


def _compare(reference, returned):
    ref_url, ref_title, ref_citation, ref_locator, ref_texts = reference
    url, title, citation, locator, texts = returned
    same_title = bool(ref_title and title == ref_title)
    same_citation = bool(ref_citation and citation == ref_citation)
    same_source = bool(ref_url and url == ref_url) or (
        not (ref_url and url) and same_title and same_citation
    )
    same_text = bool(ref_texts & texts)
    same_locator = bool(ref_locator and locator == ref_locator)
    if same_source and same_text:
        return "exact_text", True
    if same_source and same_locator and not (ref_texts and texts):
        return "exact_locator", True
    if same_source:
        return ("same_source_and_locator" if same_locator else "same_source"), False
    if same_title or same_citation or same_text:
        return "similar_reference", False
    return None, False


def _review_pairs(reviewed, references, returned):
    pairs = []
    reference_rows, returned_indices = set(), set()
    for pair in reviewed:
        if not isinstance(pair, dict):
            raise ValueError("Each reviewed match must be an object")
        row, index = pair.get("reference_row"), pair.get("returned_index")
        if (
            type(row) is not int or row not in references or row in reference_rows
            or type(index) is not int or index not in returned or index in returned_indices
        ):
            raise ValueError("Reviewed matches must pair distinct valid reference rows and returned indices")
        reference_rows.add(row)
        returned_indices.add(index)
        pairs.append({"reference_row": row, "returned_index": index, "basis": "reviewed"})
    return pairs, reference_rows, returned_indices


def _score(references, evidence, reviewed):
    reference_keys = {row: _evidence_key(item) for row, item in references.items()}
    returned_keys = {index: _evidence_key(item) for index, item in enumerate(evidence, 1)}
    matched, used_rows, used_indices = _review_pairs(reviewed, reference_keys, returned_keys)

    candidates, exact = [], []
    for index, returned in returned_keys.items():
        if index in used_indices:
            continue
        for row, reference in reference_keys.items():
            basis, confirmed = _compare(reference, returned)
            if basis is None:
                continue
            pair = {"reference_row": row, "returned_index": index, "basis": basis}
            candidates.append(pair)
            if confirmed and row not in used_rows:
                exact.append(pair)

    row_counts = Counter(pair["reference_row"] for pair in exact)
    index_counts = Counter(pair["returned_index"] for pair in exact)
    for pair in exact:
        row, index = pair["reference_row"], pair["returned_index"]
        if row_counts[row] == index_counts[index] == 1:
            matched.append(pair)
            used_rows.add(row)
            used_indices.add(index)

    possible = [pair for pair in candidates if pair["returned_index"] not in used_indices]
    needs_review = {pair["returned_index"] for pair in possible}
    novel, unknown = [], []
    for index in sorted(returned_keys.keys() - used_indices - needs_review):
        url, title, citation, _, texts = returned_keys[index]
        category = novel if texts and (url or (title and citation)) else unknown
        category.append(index)

    counts = {
        "reference": len(references), "returned": len(evidence), "matched": len(matched),
        "review_required": len(needs_review), "novel_candidates": len(novel), "unknown": len(unknown),
    }
    return {
        "counts": counts,
        "recall": counts["matched"] / counts["reference"],
        "matches": sorted(matched, key=lambda item: item["reference_row"]),
        "possible_matches": possible,
        "missed_reference_rows": sorted(reference_keys.keys() - used_rows),
        "novel_candidate_indices": novel,
        "unknown_indices": unknown,
        "returned_evidence": [_reviewable(item) for item in evidence],
    }


def _reviewable(item):
    source = item.get("source")
    if not isinstance(source, dict):
        source = {}
    return {
        **{field: item.get(field) for field in ("excerpt", "summary", "locator")},
        "source": {field: source.get(field) for field in ("title", "url", "citation")},
    }