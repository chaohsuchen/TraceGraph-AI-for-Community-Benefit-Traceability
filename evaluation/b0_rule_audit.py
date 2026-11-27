"""Audit B0 rule switches on the dev half of a split (default dev_test_split.json).

    python evaluation/b0_rule_audit.py                 # core, core + each switch, current - each switch
    python evaluation/b0_rule_audit.py --show current  # dev misses and dev false positives ('core' also works)

Only dev pages are scored, so the test half is never read. Every workbook row
is a positive (pass --validated-only to score only rows marked valid = 1).
Detection is scored on dev pages: TP = dev commitments matched, FP = every
other extraction on a dev page. Classification and field scores use matched dev
commitments.
"""
from argparse import ArgumentParser
from dataclasses import fields, replace
from functools import lru_cache
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tracegraph.b0_rules import B0_VERSION, CORE_RULES, DEFAULT_RULES, RuleSet, corpus_options, extract_document  # noqa: E402
from tracegraph.corpus import load_corpus  # noqa: E402
from tracegraph.extraction_eval import align_gold, apply_split, evaluate, load_gold, match  # noqa: E402

GOLD = ROOT / "evaluation" / "Golden Dataset (5).xlsx"
CORPUS = ROOT / "docs" / "tier1"
DEFAULT_SPLIT = ROOT / "evaluation" / "splits" / "dev_test_split.json"


@lru_cache(maxsize=None)
def corpus(merge_pages, clauses, split):
    documents = load_corpus(CORPUS, merge_pages=merge_pages, clauses=clauses)
    gold = apply_split(align_gold(load_gold(GOLD), documents), split)
    dev = [g for g in gold if g["evaluation_split"] == "dev"]
    return documents, dev


VALIDATED_ONLY = False


def dev_score(rules, split=str(DEFAULT_SPLIT)):
    options = corpus_options(rules)
    documents, dev = corpus(options["merge_pages"], options["clauses"], split)
    pages = {(g["document_id"], g["page"]) for g in dev}
    positives = [g for g in dev if g["human_validated"] or not VALIDATED_ONLY]
    preds = [e.to_dict() for d in documents for e in extract_document(d, version=rules)]
    preds = [p for p in preds if (p["document_id"], p["page"]) in pages]
    pairs = match(preds, positives)
    tp, n = len(pairs), len(preds)
    precision = tp / n if n else 0.0
    recall = tp / len(positives)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    lenient = evaluate(preds, positives)["lenient"]["all"]
    return {"tp": tp, "gold": len(positives), "fp": n - tp, "p": precision, "r": recall, "f1": f1,
            "ob_acc": lenient["obligation_type"]["accuracy"], "ob_macro": lenient["obligation_type"]["macro_f1"],
            "attr": lenient["attributes"]["macro_f1"], "ev_acc": lenient["evidence_status"]["accuracy"],
            "_pairs": pairs, "_preds": preds, "_positives": positives, "_dev": dev}


def line(name, m, base=None):
    delta = f"  dF1 {m['f1'] - base['f1']:+.3f} dOb {m['ob_macro'] - base['ob_macro']:+.3f} " \
            f"dAttr {m['attr'] - base['attr']:+.3f}" if base else ""
    return (f"{name:34} TP {m['tp']:2}/{m['gold']} FP {m['fp']:3} P {m['p']:.3f} R {m['r']:.3f} F1 {m['f1']:.3f} | "
            f"ob acc {m['ob_acc']:.3f} macro {m['ob_macro']:.3f} | attr {m['attr']:.3f} | ev {m['ev_acc']:.3f}{delta}")


def switches():
    return [f.name for f in fields(RuleSet)]


def audit(split):
    core = dev_score(CORE_RULES, split)
    print(line("core", core))
    print("\n-- core + one switch")
    for name in switches():
        print(line(f"+ {name}", dev_score(replace(CORE_RULES, **{name: True}), split), core))
    full = DEFAULT_RULES
    full_m = dev_score(full, split)
    print("\n" + line(B0_VERSION, full_m))
    print(f"-- {B0_VERSION} minus one switch")
    for name in switches():
        if getattr(full, name):
            print(line(f"- {name}", dev_score(replace(full, **{name: False}), split), full_m))


def show(rules, split):
    m = dev_score(rules, split)
    print(line("rules", m))
    print("\nMissed dev positives:")
    for g in m["_positives"]:
        if g["gold_id"] not in m["_pairs"]:
            print(f"  {g['gold_id'][-4:]} p{g['page']} {g['labels']['obligation_type'][0]} | {g['commitment_text'][:200]}")
    negatives = [g for g in m["_dev"] if VALIDATED_ONLY and not g["human_validated"]]
    used = {p["pred_id"] for p in m["_pairs"].values()}
    hits = match([p for p in m["_preds"] if p["pred_id"] not in used], negatives)
    print(f"\nDev negatives extracted ({len(hits)}/{len(negatives)}):")
    for gid, p in sorted(hits.items()):
        print(f"  {gid[-4:]} p{p['page']} {p['obligation_type']} | {p['commitment_text'][:160]}")
    print("\nMatched dev positives with wrong obligation class:")
    for g in m["_positives"]:
        p = m["_pairs"].get(g["gold_id"])
        if p and p["obligation_type"].lower() not in [x.lower() for x in g["labels"]["obligation_type"]]:
            print(f"  {g['gold_id'][-4:]} gold {g['labels']['obligation_type'][0]} pred {p['obligation_type']} "
                  f"(cue {p['obligation_cue']!r}) | {g['commitment_text'][:120]}")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", default=str(DEFAULT_SPLIT))
    parser.add_argument("--show", choices=("current", "core"), help="List dev errors for the current rules or the core.")
    parser.add_argument("--validated-only", action="store_true", help="Score only rows marked valid = 1 as positives.")
    args = parser.parse_args()
    VALIDATED_ONLY = args.validated_only
    if args.show:
        show(CORE_RULES if args.show == "core" else DEFAULT_RULES, args.split)
    else:
        audit(args.split)
