"""Replay recorded source-provider responses through the shipping PDF extraction entry point."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from tracegraph import pdf_extract as extract, pdf_review as review
from tracegraph.pdf_import import validate_rows
from .experiment import D, P, read


def verify(directory):
    docs = [extract.load_document(p) for p in sorted(directory.glob("*.pdf"))]
    by_hash = {d.file_hash: d for d in docs}
    expected = read(D/"reviewed.json")
    validate_rows(expected, by_hash)
    raw, kept = read(P/"single_pass.json"), {review.item_key(r) for r in read(P/"reviewed.json")}
    windows = read(P/"diagnostic/frozen-shadow.json")
    judgments = read(P/"diagnostic/frozen-adjudication.json")
    units = {d.file_hash: {s: i for i, s in enumerate(extract.source_units(d))} for d in docs}
    names = {Path(d.path).name: d.document_id for d in docs}
    def proposal(r):
        return {"organization": r["organization"], "description": r["description"], "citations":
                [units[r["document_sha256"]][(c["char_start"],c["char_end"])] for c in r["citations"]]}
    by_window = {}
    recorded = {}
    for w in windows:
        old = sorted(w["decisions"], key=lambda d: d["id"])
        rows = [raw[int(d["id"][1:])] for d in old]
        by_window[w["document_id"], w["range"][0]] = old, rows, w["omissions"]
        entries = [(d["id"], r) for d, r in zip(old, rows)]
        entries.extend(("s-" + review.item_key(r), r) for r in w["omissions"])
        for key, r in entries:
            if key in judgments:
                rec = proposal(r)
                recorded[r["filename"], r["organization"], r["description"], tuple(rec["citations"])] = judgments[key]

    def replay(client, prompt, packet, schema, limit):
        if prompt == review.ADJUDICATION_PROMPT:
            return {k: recorded[v["document"], v["organization"], v["claim"], tuple(v["citation_ids"])]
                    for k, v in packet.items()}
        src = packet if prompt == extract.EXTRACT else packet["source"]
        ds, rows, extras = by_window[names[src["document"]], src["target_ids"][0]]
        if prompt == extract.EXTRACT:
            return {"items": [proposal(r) for r in rows]}
        if prompt == extract.REVIEW:
            return {"keep": [i for i, r in enumerate(rows) if review.item_key(r) in kept]}
        if prompt != review.COVERAGE_PROMPT:
            raise ValueError("Unexpected source model request")
        for i, r in enumerate(rows):
            assert packet["candidates"][f"p{i:05}"]["claim"] == r["description"]
        return {"decisions": {f"p{i:05}": {"kind": d["kind"], "why": d["why"]} for i, d in enumerate(ds)},
                "omissions": [{**proposal(r), "kind": r["kind"]} for r in extras]}

    with TemporaryDirectory() as temp, patch.object(extract, "_structured", replay), patch.object(review, "structured", replay):
        out = Path(temp)
        extract.run([Path(d.path) for d in docs], out, SimpleNamespace(model="frozen-responses"), 3)
        result = read(out/"reviewed.json")
        if result != expected:
            raise ValueError("Shipping PDF workflow diverged from frozen predictions")
        with patch.object(extract, "_structured", side_effect=AssertionError("unexpected paid retry")):
            extract.run([Path(d.path) for d in docs], out, SimpleNamespace(model="frozen-responses"), 3)
            assert read(out/"reviewed.json") == result
    groups = read(D/"catalog-groups.json")
    assert set(groups) == {review.item_key(r) for r in result}
    print(f"Validated {len(result)} source rows, {len(set(groups.values()))} catalog groups, "
          f"{sum(len(r['citations']) for r in result)} exact citation occurrences and cache replay.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_directory", type=Path)
    verify(parser.parse_args().pdf_directory)
