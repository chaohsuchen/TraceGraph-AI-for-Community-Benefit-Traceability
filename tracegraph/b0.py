"""Run and evaluate the B0 rules/patterns baseline.

    python -m tracegraph.b0                     # extract + evaluate with defaults
    python -m tracegraph.b0 --output-dir DIR    # choose where results go

Outputs (default ``evaluation/results/b0/``):
  predictions.jsonl   one extracted commitment per line, with provenance
  manifest.json       corpus hashes, gold hash, extractor version, settings
  metrics.json        full metric report (lenient and strict matching)
  matches.csv         per-gold-row comparison for error analysis
  report.md           human-readable summary tables
"""

from argparse import ArgumentParser
import csv
from datetime import datetime, timezone
from json import dumps
from pathlib import Path
import platform

from .b0_rules import B0_VERSION, extract_document
from .corpus import TEXT_EXTRACTION_METHOD, file_sha256, load_corpus
from .extraction_eval import ATTRIBUTE_FIELDS, CLASS_FIELDS, align_gold, evaluate, load_gold


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "docs" / "tier1"
DEFAULT_GOLD = ROOT / "evaluation" / "Golden Dataset (5).xlsx"
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "b0"


def run(corpus_dir=DEFAULT_CORPUS, gold_path=DEFAULT_GOLD, output_dir=DEFAULT_OUTPUT, as_of_year=2026):
    documents = load_corpus(corpus_dir)
    predictions = [e.to_dict() for doc in documents for e in extract_document(doc, as_of_year)]
    gold = align_gold(load_gold(gold_path), documents)
    gold_docs = {g["document_id"] for g in gold}
    scored = [p for p in predictions if p["document_id"] in gold_docs]
    report = evaluate(scored, gold)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "predictions.jsonl", "w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(dumps(prediction, ensure_ascii=False) + "\n")
    manifest = {
        "system": "B0 rules/patterns (no generative model)",
        "extractor_version": B0_VERSION,
        "text_extraction_method": TEXT_EXTRACTION_METHOD,
        "evidence_status_as_of_year": as_of_year,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "gold_workbook": {"path": _rel(gold_path), "sha256": file_sha256(gold_path)},
        "documents": [
            {"document_id": d.document_id, "path": _rel(d.path), "sha256": d.file_hash,
             "page_count": d.page_count, "passages": len(d.passages),
             "predictions": sum(1 for p in predictions if p["document_id"] == d.document_id),
             "scored": d.document_id in gold_docs}
            for d in documents
        ],
    }
    (output_dir / "manifest.json").write_text(dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "metrics.json").write_text(dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_matches(output_dir / "matches.csv", gold, scored, report)
    (output_dir / "report.md").write_text(render_report(report, manifest), encoding="utf-8")
    return report, manifest


def _rel(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_matches(path, gold, predictions, report):
    by_id = {p["pred_id"]: p for p in predictions}
    pairs = report["lenient"]["_pairs"]
    fields = ("obligation_type",) + ATTRIBUTE_FIELDS + ("evidence_status",)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["gold_id", "evaluation_split", "human_validated", "document_id", "gold_page", "matched_pred_id",
                  "pred_page", "gold_text", "pred_text"]
        for name in fields:
            header += [f"gold_{name}", f"pred_{name}"]
        writer.writerow(header)
        for g in gold:
            p = by_id.get(pairs.get(g["gold_id"]), {})
            row = [g["gold_id"], g["evaluation_split"], int(g["human_validated"]), g["document_id"],
                   g.get("page", ""), p.get("pred_id", ""), p.get("page", ""), g["commitment_text"],
                   p.get("commitment_text", "")]
            for name in fields:
                row += [" || ".join(g["labels"][name]), p.get(name, "")]
            writer.writerow(row)


def _pct(value):
    return f"{value:.3f}"


def render_report(report, manifest):
    lines = [
        "# B0 rules/patterns baseline: evaluation",
        "",
        f"Extractor `{manifest['extractor_version']}`, text extraction `{manifest['text_extraction_method']}`, "
        f"gold workbook sha256 `{manifest['gold_workbook']['sha256'][:12]}`, generated {manifest['generated_at']}.",
        "",
        f"Gold commitments: {report['gold_items']} (unaligned: {len(report['gold_unaligned'])}). "
        f"Scored predictions (gold-covered documents only): {report['predictions']}.",
        "",
        "The gold set samples commitments rather than labelling every commitment, so unmatched predictions count "
        "as false positives and **precision is a lower bound**. `annotated pages` restricts predictions to pages "
        "that hold at least one gold commitment. *Lenient* matching needs the prediction to cover at least 50% "
        "of the gold span; *strict* needs span IoU of at least 0.5.",
        "",
        "## Commitment-level precision / recall / F1",
        "",
        "| Matching | Scope | P | R | F1 | P (annotated pages) | F1 (annotated pages) | TP | FP | FN |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode in ("lenient", "strict"):
        for scope in ("all", "anchor", "heldout"):
            m = report[mode]["commitment"][scope]
            a = m["annotated_pages"]
            lines.append(f"| {mode} | {scope} | {_pct(m['precision'])} | {_pct(m['recall'])} | {_pct(m['f1'])} | "
                         f"{_pct(a['precision'])} | {_pct(a['f1'])} | {m['tp']} | {m['fp']} | {m['fn']} |")
    lines += ["", "Recall by gold split (lenient):", ""]
    commitment = report["lenient"]["commitment"]
    for key in sorted(k for k in commitment if k.startswith("recall_")):
        m = commitment[key]
        lines.append(f"- {key[len('recall_'):]}: {_pct(m['recall'])} ({m['tp']}/{m['gold']})")

    for group in ("all", "anchor_test", "heldout", "human_validated"):
        section = report["lenient"].get(group)
        if not section:
            continue
        lines += [
            "",
            f"## Field-level results: {group} (lenient matches, n = {section['matched']})",
            "",
            "| Field | P | R | F1 | Presence F1 | Mean token F1 (both present) |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for name in ATTRIBUTE_FIELDS:
            m = section["attributes"][name]
            soft = m["mean_token_f1_when_both_present"]
            lines.append(f"| {name} | {_pct(m['precision'])} | {_pct(m['recall'])} | {_pct(m['f1'])} | "
                         f"{_pct(m['presence']['f1'])} | {'-' if soft is None else _pct(soft)} |")
        lines.append(f"| **attribute macro-F1** | | | **{_pct(section['attributes']['macro_f1'])}** | | |")
        for name in CLASS_FIELDS:
            m = section[name]
            lines.append(f"| {name} (classification) | | accuracy {_pct(m['accuracy'])} | macro-F1 "
                         f"{_pct(m['macro_f1'])} | | |")

    ob = report["lenient"]["all"]["obligation_type"]
    lines += ["", "## Obligation class, per class (all matched gold, lenient)", "",
              "| Class | P | R | F1 | Gold support |", "| --- | --- | --- | --- | --- |"]
    for label, m in ob["per_class"].items():
        lines.append(f"| {label} | {_pct(m['precision'])} | {_pct(m['recall'])} | {_pct(m['f1'])} | {m['tp'] + m['fn']} |")
    lines += ["", "## Corpus", "", "| Document | Pages | Passages | Predictions | Scored |", "| --- | --- | --- | --- | --- |"]
    for d in manifest["documents"]:
        lines.append(f"| {d['document_id']} | {d['page_count']} | {d['passages']} | {d['predictions']} | "
                     f"{'yes' if d['scored'] else 'no'} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-dir", default=str(DEFAULT_CORPUS))
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--as-of-year", type=int, default=2026,
                        help="Year used to mark dated milestones as 'not yet due'.")
    args = parser.parse_args(argv)
    report, _ = run(args.corpus_dir, args.gold, args.output_dir, args.as_of_year)
    lenient = report["lenient"]
    print(f"B0 {B0_VERSION}: {report['predictions']} scored predictions, {report['gold_items']} gold")
    for scope in ("all", "anchor", "heldout"):
        m = lenient["commitment"][scope]
        print(f"  commitment[{scope}] P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f}")
    print(f"  attribute macro-F1={lenient['all']['attributes']['macro_f1']:.3f}  "
          f"obligation macro-F1={lenient['all']['obligation_type']['macro_f1']:.3f}")
    print(f"Results written to {args.output_dir}")


if __name__ == "__main__":
    main()
