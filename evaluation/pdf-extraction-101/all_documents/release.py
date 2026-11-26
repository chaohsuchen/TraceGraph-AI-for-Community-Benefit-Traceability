"""Verify the selected catalog and replay both reference sets without model or database access."""
from hashlib import sha256
from pathlib import Path

from evaluation.score_pdf_extraction import read_json, score
from tracegraph.pdf_review import item_key
from tracegraph.tender_review import filtered
from ..diagnostic.analyze import group


HERE = Path(__file__).resolve().parent
BENCHMARK = HERE.parent
ROOT = BENCHMARK.parent.parent
SELECTED = HERE / "single-pass"


def snapshot():
    rows = read_json(SELECTED / "reviewed.json")
    group_ids = read_json(SELECTED / "catalog-groups.json")
    before = read_json(BENCHMARK / "tender/all-edits/reviewed.json")
    decisions = read_json(HERE / "source-decisions.json")
    if decisions["scope"] != "all" or rows != filtered(before, decisions):
        raise ValueError("Selected output differs from the first all-document review")
    keys = [item_key(row) for row in rows]
    if set(keys) != group_ids.keys() or len(keys) != len(group_ids):
        raise ValueError("Catalog assignments must cover the selected source rows exactly")
    groups = group([(group_ids[key], row) for key, row in zip(keys, rows)])
    original = read_json(BENCHMARK / "reference.json")["gold_rows_index"]
    additions = read_json(HERE / "reference-additions.json")
    if len({ref["gold_id"] for ref in original + additions}) != len(original) + len(additions):
        raise ValueError("Reference IDs must be unique")
    baseline = score(original, groups)
    augmented = score(original + additions, groups)
    retained_claims = {(group_ids[key], row["description"]) for key, row in zip(keys, rows)}
    audited = [{
        "reference": ref["gold_id"],
        "span_match": augmented["pairs"].get(ref["gold_id"]),
        "audited_catalog_id": ref["audited_catalog_id"],
        "audited_claim_retained": (ref["audited_catalog_id"], ref["claim"]) in retained_claims,
    } for ref in additions]
    paths = [SELECTED / name for name in ("reviewed.json", "catalog-groups.json", "archived.json")]
    paths += [HERE / "source-decisions.json", HERE / "reference-additions.json", HERE / "extract.py",
              BENCHMARK / "reference.json", BENCHMARK / "unmatched-all-edits-audit.csv",
              BENCHMARK / "freeze/freeze.lock.json", BENCHMARK / "freeze/edits.py",
              BENCHMARK / "tender/source-decisions.json", ROOT / "tracegraph/tender_review.py",
              ROOT / "evaluation/score_pdf_extraction.py"]
    hashes = {path.relative_to(ROOT).as_posix(): sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for path in paths}
    return {"version": "pdf-catalog-all-documents-single-review-1", "source_rows": len(rows),
            "sha256_lf": hashes, "original": baseline, "augmented": augmented, "audit": audited}


if __name__ == "__main__":
    actual = snapshot()
    if actual != read_json(SELECTED / "release.lock.json"):
        raise SystemExit("Frozen catalog, reference, implementation or score differs from the lock")
    for name in ("original", "augmented"):
        result = actual[name]
        print(f"{name}: {result['tp']}/{result['predictions']} precision {result['precision']:.2%}, "
              f"{result['tp']}/{result['gold']} recall {result['recall']:.2%}, F1 {result['f1']:.2%}")
