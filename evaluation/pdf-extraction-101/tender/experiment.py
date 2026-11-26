"""Source-only tender ablation on both frozen candidate inventories."""
import argparse
from pathlib import Path
from threading import Lock

from tracegraph.corpus import load_document
from tracegraph.openai_provider import OpenAIClient
from tracegraph.pdf_extract import write_json
from tracegraph.pdf_review import item_key
from tracegraph.tender_review import review
from ..freeze.edits import named, apply_edits
from ..freeze.experiment import D as FREEZE, read

D = Path(__file__).resolve().parent


def inventories():
    baseline = read(FREEZE / "reviewed.json")
    groups = read(FREEZE / "catalog-groups.json")
    named_rows = named(baseline)
    changed = apply_edits(named_rows, read(FREEZE / "source-decisions.json"), {"add", "archive", "repeat", "facet"})
    parents = {}
    def root(key):
        while key in parents:
            key = parents[key]
        return key
    def catalog_id(key):
        return groups[item_key(named_rows[key])] if key in named_rows else key
    for _, members in changed.values():
        ids = sorted({root(catalog_id(k)) for k in members})
        for child in ids[1:]:
            parents[child] = ids[0]
    return {"frozen": [(groups[item_key(r)], r) for r in baseline],
            "all_edits": [(root(catalog_id(members[0])), r) for r, members in changed.values()]}


if __name__ == "__main__":
    from tracegraph.runtime import read_credential
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf_directory", type=Path)
    p.add_argument("--openai-key-file", type=Path)
    args = p.parse_args()
    docs = {d.file_hash: d for d in (load_document(f) for f in sorted(args.pdf_directory.glob("*.pdf")))}
    rows = {item_key(r): r for records in inventories().values() for _, r in records}
    events, lock = [], Lock()
    def log(event):
        with lock:
            events.append({k: event.get(k) for k in ("model", "status", "usage", "search_enabled")})
    client = OpenAIClient(model="gpt-5.4", allow_web_search=False, reasoning_effort="low", timeout=180,
                          api_key=read_credential(args.openai_key_file), emit=log)
    try:
        audit = review(list(rows.values()), docs, client)
        write_json(D / "source-decisions.json", audit)
        print({d["document"]: d["role"] for d in audit["document_roles"].values()}, len(audit["decisions"]))
    finally:
        write_json(D / "usage.json", events)
