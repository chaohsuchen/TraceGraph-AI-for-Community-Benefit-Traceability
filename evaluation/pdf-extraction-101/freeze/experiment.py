"""One final source-only PDF edit pass. This runner does not read evaluation labels."""
import argparse
from pathlib import Path
import json
from threading import Lock

from tracegraph.corpus import load_document
from tracegraph.openai_provider import OpenAIClient
from tracegraph.pdf_extract import write_json
from .edits import named, propose
from tracegraph.pdf_review import assemble
from tracegraph.runtime import read_credential

D = Path(__file__).resolve().parent
P = D.parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def previous():
    return assemble(read(P/"single_pass.json"), read(P/"reviewed.json"),
                    read(P/"diagnostic/frozen-shadow.json"), read(P/"diagnostic/frozen-adjudication.json"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf_directory", type=Path)
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--key-file", type=Path)
    args = p.parse_args()
    docs = [load_document(path) for path in sorted(args.pdf_directory.glob("*.pdf"))]
    rows = named(previous().values())
    if {r["document_sha256"] for r in rows.values()} != {d.file_hash for d in docs}:
        raise ValueError("Unexpected PDF corpus")
    events, lock = [], Lock()
    def record(event):
        with lock:
            events.append({k: event.get(k) for k in ("model", "usage", "status", "search_enabled")})
    client = OpenAIClient(api_key=read_credential(args.key_file), model="gpt-5.4", allow_web_search=False,
                          reasoning_effort="low", timeout=240, emit=record)
    try:
        decisions = propose(docs, rows, client, args.cache, 3)
        write_json(D/"source-decisions.json", decisions)
        print("Source-only proposals and confirmations frozen:", len(decisions))
    finally:
        usage = D/"usage.json"
        write_json(usage, (read(usage) if usage.exists() else []) + events)
