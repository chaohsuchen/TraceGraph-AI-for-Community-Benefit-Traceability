"""Offline paired scoring with the unchanged 101-row source-span matcher."""
from collections import Counter

from tracegraph.pdf_review import item_key
from tracegraph.pdf_extract import write_json
from tracegraph.tender_review import export_review, filtered
from ..diagnostic.analyze import group, measure, table
from .experiment import D, FREEZE, inventories, read


def run():
    audit = read(D / "source-decisions.json")
    reference = read(FREEZE.parent / "reference.json")["gold_rows_index"]
    old_scores = read(FREEZE / "comparison.json")["policies"]
    results, ledger = {}, []
    for version, records in inventories().items():
        baseline = old_scores[{"all_edits": "all", "frozen": "previous"}[version]]
        paired = baseline["pairs"]
        before = measure(reference, group(records), set(paired))
        assert before["pairs"] == paired and before["predictions"] == baseline["predictions"]
        kept_keys = {item_key(r) for r in filtered([r for _, r in records], audit)}
        kept = [(pid, r) for pid, r in records if item_key(r) in kept_keys]
        after = measure(reference, group(kept), set(paired))
        by_id = {pid: gid for gid, pid in paired.items()}
        decisions = Counter()
        for pid, row in records:
            if audit["document_roles"][row["document_sha256"]]["role"] != "SOLICITATION":
                assert item_key(row) in kept_keys
                continue
            d = audit["decisions"][item_key(row)]
            cite = row["citations"][int(d["passage_id"][1:])]
            decisions[d["decision"]] += 1
            ledger.append({"variant": version, "catalog_id": pid, "source_row": item_key(row),
                           "page": cite["page"], "decision": d["decision"], "original_group_match": by_id.get(pid,""),
                           "claim": row["description"], "reason": d["reason"], "quote": cite["excerpt"]})
        results[version] = {"before": before, "after": after, "tender_source_row_decisions": dict(decisions)}
        if version == "all_edits":
            exported = export_review([r for _, r in records], audit, D / "all-edits")
            assert exported == [r for _, r in kept]
            write_json(D / "all-edits/catalog-groups.json", {item_key(r): pid for pid, r in kept})
        print(version, before["tp"], before["predictions"], "->", after["tp"], after["predictions"],
              "gained", after["gained"], "lost", after["lost"])
    write_json(D / "metrics.json", results)
    table(D / "candidate-decisions.csv", ledger)


if __name__ == "__main__":
    run()
