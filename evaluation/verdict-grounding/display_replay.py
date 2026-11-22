"""Replay final presentation on each archived v7 judgment without repeating any assessor calls."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

from replay import CASES, ROOT, REPEATS, SYNTHETIC, TRANSFER, assessor_inputs, assessor_pin, digest, grade, read, source_dir, synthetic_inputs, write
from summarize import costs


PANELS = {"v7": CASES, "v7-transfer": TRANSFER, "v7-r2": REPEATS, "v7-r3": REPEATS,
          "v7-negative": (SYNTHETIC,), "integration-v7": ("TG-GOLD-H017",)}
MANIFEST = ROOT/"v7-display-replay.json"


def inputs(args, panel, case):
    if case == SYNTHETIC:
        return synthetic_inputs()
    loc = source_dir(args.study, case)
    ctx, state, day = assessor_inputs(loc)
    if panel == "integration-v7":
        augmented = read(args.coverage/"results/discover"/case/"research.json")["graph"]["findings"]
        initial = read(loc/"research.json")["graph"]["findings"]
        assert augmented[:len(initial)] == initial
        state["findings"].extend(augmented[len(initial):])
    return ctx, state, day


def render(args, compare):
    from tracegraph.reconcile import EvidenceReconciler
    runtime = assessor_pin(args.runtime)
    prior = read(ROOT/"v7-integrity.json")
    if not compare:
        assert runtime == prior["runtime"]
    entries = []
    for panel, cases in PANELS.items():
        for case in cases:
            source = ROOT/"runs"/panel/case/"assessment.json"
            receipt = read(source)
            context, state, day = inputs(args, panel, case)
            assert receipt["runtime"] == prior["runtime"] and receipt["inputs_sha256"] == digest([context,state,day])
            original = receipt["displayed"]
            # A successful verifier appends the accepted literal quote in the original rationale.
            # A rejected verifier produces the existing fallback. Reproduce the original bytes first.
            def verify(_context, _support, _basis, quote):
                return original["label"] == "REFUTED" and f'“{quote.strip()}”' in original["reasoning"]
            rec = EvidenceReconciler(None, verify_refutation=verify, verify_conflicts=lambda *_: None)
            displayed = {**rec._check_assessment(context, state, deepcopy(receipt["trace"])), "verification_date":day}
            assert displayed["label"] == original["label"]
            changed = displayed != original
            if not compare:
                assert not changed, f"Baseline replay mismatch: {panel}/{case}"
            if compare and changed:
                write(ROOT/"runs"/(panel+"-display")/case/"assessment.json",
                      {"case":case,"status":"COMPLETED","source_trace_sha256":digest(receipt["trace"]),
                       "runtime":runtime,"calls":[],"displayed":displayed})
            entries.append({"panel":panel,"case":case,"trace_sha256":digest(receipt["trace"]),
                            "original_display_sha256":digest(original),"display_sha256":digest(displayed),
                            "changed":changed})
    assert len(entries) == 18
    if compare:
        write(MANIFEST,{"runtime":runtime,"upstream_runtime":prior["runtime"],"entries":entries})
    print(f"{len(entries)} traces; {sum(e['changed'] for e in entries)} changed")


def score_changes(args):
    from tracegraph.nli_judge import JUDGE_VERSION, rubric_digest
    from tracegraph.nli_packet import prediction_from_research
    from tracegraph.nli_score import score_case
    manifest = read(MANIFEST)
    totals = Counter()
    events = []
    for entry in manifest["entries"]:
        case, panel = entry["case"], entry["panel"]
        loc = ROOT/"runs"/panel/case
        folder = args.coverage/"results/discover"/case if panel=="integration-v7" else source_dir(args.study,case)
        if entry["changed"]:
            args.arm = panel+"-display"
            loc = ROOT/"runs"/args.arm/case
            if args.phase=="judge" and not (loc/"judge.json").exists():
                print(case, grade(args,case,folder),flush=True)
                continue
        j = read(loc/"judge.json")
        assert j["version"] == JUDGE_VERSION and j["rubric_sha256"] == rubric_digest()
        assert j["displayed_sha256"] == entry["display_sha256"]
        if entry["changed"]:
            assert case != SYNTHETIC, "An additional changed synthetic judgment needs its new evidence grade verified."
            a, raw, saved = read(loc/"assessment.json"), read(folder/"research.json"), read(folder/"judgment.json")
            assert a["source_trace_sha256"] == entry["trace_sha256"] and j["research_sha256"] == digest(raw)
            prediction, _ = prediction_from_research(raw)
            prediction["assessment"] = a["displayed"]
            evidence = {**saved["evidence"],"protocol":"owned-quotes-v1"}
            assert j["evidence_judgment_sha256"] == digest(evidence)
            assert j["score"] == score_case(read(folder/"reference.json"),prediction,{**evidence,"verdict":j["result"]})
            events.extend(j["calls"])
        totals[j["result"]["label"]] += 1
    if args.phase=="audit":
        assert sum(totals.values()) == len(manifest["entries"]) and len({e["request_id"] for e in events}) == len(events)
        manifest.update(graded=dict(totals), additional_usage=costs(events))
        write(MANIFEST, manifest)
        print(dict(totals),costs(events))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=("baseline","compare","judge","audit"))
    for name in ("study","coverage","runtime"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--key-file",type=Path)
    options = parser.parse_args()
    sys.path.insert(0,str(options.runtime.resolve()))
    if options.phase in ("baseline","compare"):
        render(options,options.phase=="compare")
    else:
        score_changes(options)
