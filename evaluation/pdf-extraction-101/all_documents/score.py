"""Score a strict subset of the existing catalog, using its unchanged group IDs."""
from collections import Counter

from tracegraph.pdf_extract import write_json
from tracegraph.pdf_review import item_key
from tracegraph.tender_review import export_review
from ..diagnostic.analyze import group, measure, table
from .experiment import D, TENDER, read


def run():
    rows = read(TENDER / "all-edits/reviewed.json")
    assignments = read(TENDER / "all-edits/catalog-groups.json")
    prior_audit = read(TENDER / "source-decisions.json")
    first = read(D / "source-decisions.json")
    audit = read(D / "confirmed-decisions.json")
    keys = [item_key(r) for r in rows]
    assert set(keys) == set(assignments) == set(audit["decisions"]) == set(first["decisions"])
    assert len(set(keys)) == len(keys)
    ref = read(TENDER.parent / "reference.json")["gold_rows_index"]
    tender_score = read(TENDER / "metrics.json")["all_edits"]["after"]
    def evaluate(records):
        pairs = [(assignments[item_key(r)], r) for r in records]
        return measure(ref, group(pairs), set(tender_score["pairs"]))
    before = evaluate(rows)
    assert before["pairs"] == tender_score["pairs"] and before["predictions"] == tender_score["predictions"]
    first_rows = export_review(rows, first, D / "single-pass")
    initial = evaluate(first_rows)
    write_json(D / "single-pass/catalog-groups.json", {item_key(r): assignments[item_key(r)] for r in first_rows})
    kept = export_review(rows, audit, D / "all-edits")
    archived = read(D / "all-edits/archived.json")
    assert kept == [r for r in rows if audit["decisions"][item_key(r)]["decision"] != "ARCHIVE"]
    assert archived == [{"record": r, "review": audit["decisions"][item_key(r)]}
                        for r in rows if audit["decisions"][item_key(r)]["decision"] == "ARCHIVE"]
    after = evaluate(kept)
    groups = {item_key(r): assignments[item_key(r)] for r in kept}
    write_json(D / "all-edits/catalog-groups.json", groups)
    previous_match = {pid: gid for gid, pid in before["pairs"].items()}
    decision_counts, ledger = {}, []
    for r, key in zip(rows, keys):
        d = audit["decisions"][key]
        role = audit["document_roles"][r["document_sha256"]]["role"]
        if role == "SOLICITATION":
            assert d == prior_audit["decisions"][key] and d["decision"] != "ARCHIVE"
        cite = r["citations"][int(d["passage_id"][1:])]
        decision_counts.setdefault(r["document_id"], Counter())[d["decision"]] += 1
        ledger.append({"document": r["document_id"], "page": cite["page"], "source_row": key,
                       "catalog_id": assignments[key], "before_gold_id": previous_match.get(assignments[key], ""),
                       "source_decision_reused": role == "SOLICITATION",
                       "initial_decision": first["decisions"][key]["decision"], "decision": d["decision"],
                       "claim": r["description"], "reason": d["reason"], "quote": cite["excerpt"]})
    table(D / "candidate-decisions.csv", ledger)
    write_json(D / "metrics.json", {"before": before, "initial": initial, "after": after,
                                    "decisions_by_document": decision_counts})
    print(f"{before['tp']}/{before['predictions']} -> {after['tp']}/{after['predictions']}",
          "gained", after["gained"], "lost", after["lost"])


if __name__ == "__main__":
    run()
