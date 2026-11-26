"""Compare frozen PDF predictions against the aligned 101-row extraction benchmark."""

import argparse
from collections import defaultdict
import json
from pathlib import Path


def intervals(spans):
    """Union intervals exactly; never give credit for gaps between cited passages."""
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def metrics(tp, predicted, gold):
    return {
        "predictions": predicted, "gold": gold, "tp": tp, "fp": predicted - tp, "fn": gold - tp,
        "precision": tp / predicted if predicted else 0,
        "recall": tp / gold if gold else 0,
        "f1": 2 * tp / (predicted + gold) if predicted + gold else 0,
    }


def score(gold, groups):
    candidates = []
    for g in gold:
        a, b = g["span"]
        for pid, docs in groups.items():
            spans = intervals(docs.get(g["document_id"], []))
            overlap = sum(max(0, min(y, b) - max(x, a)) for x, y in spans)
            if 2 * overlap >= b - a:
                length = sum(y - x for x, y in spans)
                coverage = overlap / (b - a)
                iou = overlap / (b - a + length - overlap)
                candidates.append((-coverage, -iou, g["gold_id"], pid))
    pairs, used = {}, set()
    for _, _, gid, pid in sorted(candidates):
        if gid not in pairs and pid not in used:
            pairs[gid] = pid
            used.add(pid)
    return {**metrics(len(pairs), len(groups), len(gold)), "pairs": pairs}


def stages(reference, frozen, imported=None):
    gold = reference["gold_rows_index"]
    result = {"B0": reference["systems"]["B0"]["all"]}
    stages_to_score = ["single_pass", "reviewed"]
    if imported is not None:
        stages_to_score.append("catalog")
    for stage in stages_to_score:
        filename = "reviewed" if stage == "catalog" else stage
        rows = read_json(frozen / (filename + ".json"))
        groups = defaultdict(lambda: defaultdict(list))
        for i, row in enumerate(rows):
            if stage == "catalog":
                from tracegraph.pdf_import import item_key
                pid = imported[item_key(row)]["commitment_id"]
                if not pid:
                    continue
            else:
                pid = f"p{i:05}"
            groups[pid][row["document_id"]].extend((c["char_start"], c["char_end"]) for c in row["citations"])
        result[stage] = score(gold, groups)
        result[stage]["by_document"] = {}
        for doc in sorted({g["document_id"] for g in gold}):
            part = score([g for g in gold if g["document_id"] == doc],
                         {pid: {doc: docs[doc]} for pid, docs in groups.items() if doc in docs})
            part.pop("pairs")
            result[stage]["by_document"][doc] = part
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("reference", type=Path)
    p.add_argument("frozen", type=Path)
    p.add_argument("--import-report", type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    result = stages(read_json(args.reference), args.frozen,
                    read_json(args.import_report) if args.import_report else None)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for name, result_stage in result.items():
        print(name, {k: v for k, v in result_stage.items() if k not in ("pairs", "by_document")})
