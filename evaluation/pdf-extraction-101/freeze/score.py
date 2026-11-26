"""Compare all predeclared policies and seal or verify the winning PDF snapshot offline."""
import argparse
from collections import Counter
from hashlib import sha256
from importlib import import_module
import json

from tracegraph.pdf_review import item_key
from .edits import apply_edits, named
from .experiment import D, P, previous, read

audit = import_module("evaluation.pdf-extraction-101.diagnostic.analyze")
POLICIES = {
    "previous": [], "add_only": ["add"], "archive_only": ["archive"],
    "add_archive": ["add", "archive"], "add_archive_facets": ["add", "archive", "facet"],
    "all": ["add", "archive", "facet", "repeat"], "no_add": ["archive", "facet", "repeat"]}


def render():
    retained = previous()
    rows = named(retained.values())
    raw = read(P/"single_pass.json")
    assignments = read(P/"import.json")
    original = {item_key(r): k for k, r in retained.items()}
    if len(original) != len(retained):
        raise ValueError("Frozen inputs contain identical adjudicated rows")
    prior_ids = {}
    for rid, r in rows.items():
        key = original[item_key(r)]
        cid = key
        if key.startswith("p"):
            cid = assignments.get(item_key(raw[int(key[1:])]), {}).get("commitment_id") or key
        prior_ids[rid] = cid

    def catalog(output):
        # Retain legacy aliases for surviving rows. Unify IDs solely for explicitly accepted merges.
        parent = {}
        def root(key):
            while key in parent:
                key = parent[key]
            return key
        for _, members in output.values():
            ids = sorted({root(prior_ids.get(k, k)) for k in members})
            for alias in ids[1:]:
                parent[alias] = ids[0]
        return [(root(prior_ids.get(members[0], members[0])), row) for row, members in output.values()]

    edits = read(D/"source-decisions.json")
    golden = read(P/"reference.json")["gold_rows_index"]
    baseline = read(P/"diagnostic/metrics.json")["results"]["adjudicated"]
    results = {}
    for name, actions in POLICIES.items():
        records = catalog(apply_edits(rows, edits, set(actions)))
        results[name] = audit.measure(golden, audit.group(records), set(baseline["pairs"]))
    if results["previous"]["pairs"] != baseline["pairs"] or results["previous"]["predictions"] != 184:
        raise ValueError("Could not reproduce pre-round baseline")
    eligible = [name for name, x in results.items() if not x["lost"]]
    winner = max(eligible, key=lambda k: (results[k]["precision"], results[k]["tp"], -len(POLICIES[k])))
    records = catalog(apply_edits(rows, edits, set(POLICIES[winner])))
    report = {"reference_rows": len(golden), "winner": winner, "actions": POLICIES[winner],
              "received": dict(Counter(x["action"] for x in edits)),
              "confirmed": dict(Counter(x["action"] for x in edits if x["accept"])), "policies": results}
    return {"comparison.json": report, "reviewed.json": [r for _, r in records],
            "catalog-groups.json": {item_key(r): pid for pid, r in records}}


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def freeze(seal):
    root = P.parent.parent
    outputs = {k: serialized(v) for k, v in render().items()}
    pinned = [P/"manifest.json", P/"reference.json", P/"single_pass.json", P/"reviewed.json",
              P/"import.json", P/"diagnostic/frozen-shadow.json", P/"diagnostic/frozen-adjudication.json",
              P/"diagnostic/metrics.json", P/"diagnostic/analyze.py", D/"source-decisions.json",
              D/"ROUND.md", D/"experiment.py", D/"edits.py", D/"score.py", D/"verify_pipeline.py",
              P.parent/"score_pdf_extraction.py",
              *(root/"tracegraph"/n for n in ("corpus.py", "pdf_review.py", "pdf_extract.py",
                                              "pdf_import.py"))]
    inputs = {p.relative_to(root).as_posix(): sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for p in pinned}
    lock = serialized({"version": "pdf-catalog-freeze-1", "model": "gpt-5.4", "reasoning_effort": "low",
                       "inputs_sha256": inputs, "output_sha256": {k: sha256(v).hexdigest() for k, v in outputs.items()}})
    outputs["freeze.lock.json"] = lock
    if seal and (D/"freeze.lock.json").exists():
        raise ValueError("Snapshot already sealed; use default verification")
    for name, data in outputs.items():
        if seal:
            (D/name).write_bytes(data)
        elif (D/name).read_bytes().replace(b"\r\n", b"\n") != data:
            raise ValueError(f"Freeze mismatch: {name}")
    report = json.loads(outputs["comparison.json"])
    print("Frozen policy:", report["winner"], report["actions"])
    for name, v in report["policies"].items():
        print(name, v["tp"], v["predictions"], len(v["gained"]), len(v["lost"]))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seal", action="store_true")
    freeze(p.parse_args().seal)
