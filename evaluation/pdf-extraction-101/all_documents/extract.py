"""Extract a new trusted-PDF catalog using the frozen all-document review policy."""
import argparse
import json
from pathlib import Path

from tracegraph.corpus import load_document
from tracegraph.openai_provider import OpenAIClient
from tracegraph.pdf_extract import run as extract, write_json
from tracegraph.runtime import read_credential
from tracegraph.tender_review import export_review, review
from ..freeze.edits import apply_edits, named, propose


def run(paths, output, client, workers):
    base = output / "extraction"
    extract(paths, base, client, workers)
    documents = {d.file_hash: d for d in map(load_document, paths)}
    candidates = named(json.loads((base / "reviewed.json").read_text(encoding="utf-8")))
    edits = propose(documents.values(), candidates, client, output / "edit-cache", workers)
    write_json(output / "edits.json", edits)
    edited = apply_edits(candidates, edits, {"add", "archive", "facet", "repeat"})
    rows = [r for r, _ in edited.values()]
    decisions = review(rows, documents, client, scope="all", confirm=False)
    write_json(output / "review-decisions.json", decisions)
    return export_review(rows, decisions, output)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf", nargs="+", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--openai-key-file", type=Path)
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()
    client = OpenAIClient(api_key=read_credential(args.openai_key_file), model="gpt-5.4",
                          allow_web_search=False, reasoning_effort="low", timeout=240)
    kept = run(args.pdf, args.output, client, args.workers)
    print(f"Saved {len(kept)} source rows for catalog identity resolution")
