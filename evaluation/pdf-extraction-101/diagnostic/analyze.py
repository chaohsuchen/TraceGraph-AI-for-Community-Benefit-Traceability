"""Replay extraction policies against frozen source decisions. No model or database calls."""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from evaluation.score_pdf_extraction import intervals, metrics, read_json, score
from tracegraph.pdf_import import item_key

D = Path(__file__).resolve().parent
P = D.parent
PUBLIC = {"undertaking", "project_target", "regulatory_condition", "proposed_condition",
          "documented_agreement"}

# Manual reading of the original 39 misses, before the shadow scores were known.
# Categories partition failures of the frozen scorer. They do not assert a unique model cause.
LOSS = {
    "quantified terms or targets": "A006 A009 A016 H005 A028 A032 A046 A047 A048 A055 H025",
    "reported partnership or agreement": "A035 A050 H029",
    "scope, timing, contingency or qualified position": "A003 A004 A024 A022 A045 A039 A056 A044 A036 A053 A001",
    "similar fact at another location; modality needs checking": "A051",
    "partial citation overlap": "A011",
    "bundled independent facts competing for one prediction": "A031",
    "identical duplicate Gold row": "A019@row102",
    "review loss: oversight, guarantee or binding terms": "A023 A008 A020 A018 H013 A052 H023 H024",
    "review loss: broad infrastructure claims also expressed elsewhere": "A041 A043",
}
LOSS_BY_ID = {"TG-GOLD-" + k: tag for tag, ids in LOSS.items() for k in ids.split()}


def dumps(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def table(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def overlap(gold, spans):
    a, b = gold["span"]
    spans = intervals(spans)
    common = sum(max(0, min(y, b) - max(x, a)) for x, y in spans)
    return common / (b - a), common / (b - a + sum(y - x for x, y in spans) - common)


def strict_score(gold, groups):
    candidates = []
    for g in gold:
        for pid, docs in groups.items():
            cover, iou = overlap(g, docs.get(g["document_id"], []))
            if cover >= 0.5 and iou >= 0.5:
                candidates.append((-cover, -iou, g["gold_id"], pid))
    used, matches = set(), {}
    for _, _, gid, pid in sorted(candidates):
        if gid not in matches and pid not in used:
            used.add(pid)
            matches[gid] = pid
    return metrics(len(matches), len(groups), len(gold))


def group(records):
    grouped = defaultdict(lambda: defaultdict(list))
    for pid, row in records:
        grouped[pid][row["document_id"]].extend((c["char_start"], c["char_end"]) for c in row["citations"])
    return grouped


def measure(gold, groups, base):
    result = score(gold, groups)
    gids = set(result["pairs"])
    result["gained"] = sorted(gids - base)
    result["lost"] = sorted(base - gids)
    result["strict_overlap"] = strict_score(gold, groups)
    result["by_document"] = {}
    for doc in sorted({g["document_id"] for g in gold}):
        local = score([g for g in gold if g["document_id"] == doc],
                      {pid: {doc: docs[doc]} for pid, docs in groups.items() if doc in docs})
        local.pop("pairs")
        result["by_document"][doc] = local
    result["by_frozen_split"] = {split: {"hits": sum(g["gold_id"] in gids for g in gold if
                                                    g["evaluation_split"] == split),
                                        "gold": sum(g["evaluation_split"] == split for g in gold)}
                                 for split in sorted({g["evaluation_split"] for g in gold})}
    return result


def run(shadow, output, adjudication=None):
    gold = read_json(P / "reference.json")["gold_rows_index"]
    official = read_json(P / "scores.json")
    one, imported = read_json(P / "single_pass.json"), read_json(P / "import.json")
    windows = read_json(shadow)
    decisions = {d["id"]: d for w in windows for d in w["decisions"]}
    assert len(decisions) == sum(len(w["decisions"]) for w in windows) == len(one)
    old = {f"p{i:05}": r for i, r in enumerate(one)}
    assert set(old) == set(decisions)
    doc_for = {d["id"]: w["document_id"] for w in windows for d in w["decisions"]}
    assert all(row["document_id"] == doc_for[k] for k, row in old.items())
    cids = {k: imported[item_key(r)]["commitment_id"] for k, r in old.items() if item_key(r) in imported}
    assert len(cids) == len(imported)
    accepted = {k for k, d in decisions.items() if d["kind"] in PUBLIC}
    current = [(pid, old[k]) for k, pid in cids.items()]
    filtered = [(pid, old[k]) for k, pid in cids.items() if k in accepted]
    reclassified = [(cids.get(k, k), old[k]) for k in old if k in accepted]
    extra = {}
    key_to_pid = {item_key(r): cids.get(k, k) for k, r in old.items()}
    for w in windows:
        for row in w["omissions"]:
            key = item_key(row)
            pid = key_to_pid.setdefault(key, f"s-{key}")
            extra.setdefault(pid, []).append(row)
    supplements = [(pid, r) for pid, rows in extra.items() for r in rows]
    configs = {"current": current, "filter_current": filtered, "reclassify_raw": reclassified,
               "rescan_only": current + supplements, "combined": reclassified + supplements}
    checked = {}
    if adjudication:
        checked = read_json(adjudication)
        disagreements = set(cids) ^ accepted
        assert set(checked) == disagreements | {"s-" + item_key(r) for _, r in supplements}
        def retained(key):
            return key in checked and checked[key]["action"] in {"keep", "qualify"}
        resolved = [(cids.get(k, k), old[k]) for k in old
                    if k in (set(cids) & accepted) or retained(k)]
        configs["adjudicated"] = resolved + [(pid, r) for pid, r in supplements if retained("s-" + item_key(r))]
    results = {k: measure(gold, group(records), set(official["catalog"]["pairs"]))
               for k, records in configs.items()}
    assert results["current"]["pairs"] == official["catalog"]["pairs"]
    assert set(LOSS_BY_ID) == {g["gold_id"] for g in gold} - set(results["current"]["pairs"])
    output.mkdir(parents=True, exist_ok=True)
    for stage, value in results.items():
        print(stage, value["tp"], value["predictions"], len(value["gained"]), len(value["lost"]))

    def inv(matches):
        return {pid: gid for gid, pid in matches.items()}

    raw_match = inv(official["single_pass"]["pairs"])
    final_match = inv(results["current"]["pairs"])
    combined_match = inv(results["combined"]["pairs"])
    candidate_rows = []
    for k, row in old.items():
        spans = [(c["char_start"], c["char_end"]) for c in row["citations"]]
        eligible = [g["gold_id"] for g in gold if g["document_id"] == row["document_id"]
                    and overlap(g, spans)[0] >= 0.5]
        candidate_rows.append({"raw_id": k, "document": row["document_id"], "page": row["citations"][0]["page"],
                               "organization": row["organization"], "claim": row["description"],
                               "kind": decisions[k]["kind"], "reason": decisions[k]["why"],
                               "current_catalog_id": cids.get(k, ""), "raw_paired_gold": raw_match.get(k, ""),
                               "current_paired_gold_for_catalog_id": final_match.get(cids.get(k), ""),
                               "all_span_eligible_gold_ids": " ".join(eligible),
                               "adjudication": checked.get(k, {}).get("action", ""),
                               "adjudicated_wording": checked.get(k, {}).get("claim", "")})
    table(output / "candidate-audit.csv", candidate_rows)
    rescue_rows = [{"candidate": pid, "document": row["document_id"], "page": row["citations"][0]["page"],
                    "kind": row["kind"], "organization": row["organization"], "claim": row["description"],
                    "combined_paired_gold": combined_match.get(pid, ""),
                    "adjudication": checked.get("s-" + item_key(row), {}).get("action", ""),
                    "adjudicated_wording": checked.get("s-" + item_key(row), {}).get("claim", ""),
                    "citations": " | ".join(c["excerpt"] for c in row["citations"])}
                   for pid, row in supplements]
    if rescue_rows:
        table(output / "rescue-audit.csv", rescue_rows)
    raw_groups = group(old.items())
    gold_rows = []
    for g in gold:
        gid, doc = g["gold_id"], g["document_id"]
        hits = {stage: value["pairs"].get(gid, "") for stage, value in results.items()}
        raw_cover = max(overlap(g, docs.get(doc, []))[0] for docs in raw_groups.values())
        gold_rows.append({"gold_id": gid, "row": g["row"], "document": doc, "page": g["page"],
                          "split": g["evaluation_split"], "raw_best_span_coverage": round(raw_cover, 4),
                          "first_pass": official["single_pass"]["pairs"].get(gid, ""),
                          "reviewed": official["reviewed"]["pairs"].get(gid, ""),
                          "current_miss_diagnosis": LOSS_BY_ID.get(gid, ""), **hits})
    table(output / "gold-audit.csv", gold_rows)
    stats = {
        "shadow_sha256": hashlib.sha256(shadow.read_bytes()).hexdigest(), "windows": len(windows),
        "raw_decisions": len(old), "omissions_anchored": len(supplements),
        "omissions_distinct_exact_keys": len(extra),
        "kind_by_current_raw_membership": {
            scope: dict(Counter(decisions[k]["kind"] for k in keys)) for scope, keys in
            [("all", old), ("current", cids), ("dropped_by_current", set(old) - set(cids))]},
        "current_unmatched_groups": {
            "total": len(set(cids.values()) - set(final_match)),
            "has_in_scope_raw_member": len({cids[k] for k in set(cids) & accepted} - set(final_match))},
        "miss_diagnoses": dict(Counter(LOSS_BY_ID.values())), "results": results}
    if checked:
        stats["adjudication_sha256"] = hashlib.sha256(adjudication.read_bytes()).hexdigest()
        stats["adjudication_actions"] = dict(Counter(v["action"] for v in checked.values()))
    dumps(output / "metrics.json", stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow", type=Path, default=D / "frozen-shadow.json")
    parser.add_argument("--output", type=Path, default=D)
    parser.add_argument("--adjudication", type=Path, default=D / "frozen-adjudication.json")
    args = parser.parse_args()
    run(args.shadow, args.output, args.adjudication)
