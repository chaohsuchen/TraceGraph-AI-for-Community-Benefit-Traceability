"""Extend the frozen tender review to every survivor, using source material only."""
import argparse
from pathlib import Path
from threading import Lock

from tracegraph.corpus import load_document
from tracegraph.openai_provider import OpenAIClient
from tracegraph.pdf_extract import write_json
from tracegraph.tender_review import confirm_archives, extend_review
from ..tender.experiment import D as TENDER, read

D = Path(__file__).resolve().parent


if __name__ == "__main__":
    from tracegraph.runtime import read_credential
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf_directory", type=Path)
    p.add_argument("--openai-key-file", type=Path)
    p.add_argument("--confirm-only", action="store_true", help="Reuse the recorded initial review.")
    args = p.parse_args()
    docs = {d.file_hash: d for d in (load_document(f) for f in sorted(args.pdf_directory.glob("*.pdf")))}
    rows = read(TENDER / "all-edits/reviewed.json")
    prior = read(TENDER / "source-decisions.json")
    events, lock = [], Lock()
    def log(event):
        with lock:
            events.append({k: event.get(k) for k in ("model", "status", "usage", "search_enabled")})
    client = OpenAIClient(model=prior["model"], allow_web_search=False, reasoning_effort="low",
                          timeout=180, api_key=read_credential(args.openai_key_file), emit=log)
    try:
        if args.confirm_only:
            first = read(D / "source-decisions.json")
        else:
            first = extend_review(rows, docs, client, prior)
            write_json(D / "source-decisions.json", first)
        audit = confirm_archives(rows, docs, client, first)
        write_json(D / "confirmed-decisions.json", audit)
        print("Reviewed source rows", len(audit["decisions"]), "archive checks", len(audit["archive_checks"]))
    finally:
        write_json(D / ("confirmation-usage.json" if args.confirm_only else "usage.json"), events)
