"""Recompute all final panel scores, verify frozen inputs and total the unique recorded calls."""
import argparse
from collections import Counter
from hashlib import sha256
from pathlib import Path
import sys
from zipfile import ZipFile

from replay import CASES, MODEL, REPEATS, ROOT, SYNTHETIC, TRANSFER, assessor_inputs, digest, read, source_dir, synthetic_inputs, write
from summarize import costs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--judge-runtime", required=True, type=Path)
    parser.add_argument("--version", choices=("v4", "v5", "v6", "v7"), required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.judge_runtime.resolve()))
    from tracegraph.nli_judge import JUDGE_VERSION, rubric_digest
    from tracegraph.nli_packet import prediction_from_research
    from tracegraph.nli_score import score_case
    v = args.version
    with ZipFile(args.study / "runtime-candidate.zip") as base, ZipFile(ROOT/f"scored-{v}-overlay.zip") as overlay:
        runtimes = {}
        for mod in ("providers.py", "reconcile.py", "openai_provider.py", "agent.py"):
            name = "tracegraph/"+mod
            data = (overlay if name in overlay.namelist() else base).read(name)
            runtimes[mod] = sha256(data).hexdigest()
    panels = {v:CASES, v+"-transfer":TRANSFER, v+"-r2":REPEATS, v+"-r3":REPEATS, v+"-negative":[SYNTHETIC]}
    receipts, totals, provider_calls = {}, Counter(), []
    for panel, cases in panels.items():
        receipts[panel] = {}
        for cid in cases:
            loc = ROOT/"runs"/panel/cid
            a, j = read(loc/"assessment.json"), read(loc/"judge.json")
            assert a["status"] == "COMPLETED" and a["runtime"] == runtimes and a["model"] == j["model"] == MODEL
            assert j["version"] == JUDGE_VERSION and j["rubric_sha256"] == rubric_digest()
            assert j["displayed_sha256"] == digest(a["displayed"])
            if cid == SYNTHETIC:
                fixture = read(ROOT/"fixtures/scope-negative.json")
                context, state, day = synthetic_inputs()
                ref = {"case_id":cid,"query":fixture["query"],"conditions":fixture["evaluator_only"]["conditions"],
                       "facts":[{"id":"G1","text":fixture["passages"][0]["quote"]}]}
                raw = {"query":fixture["query"], "outcome":"RESOLVED",
                       "graph":{**state,"organization":{"name":fixture["organization"]},
                                "commitment":context["commitment"],"assessment":a["displayed"]}}
                evidence = j["evidence"]
            else:
                old = source_dir(args.study,cid)
                context, state, day = assessor_inputs(old)
                ref, raw = read(old/"reference.json"), read(old/"research.json")
                evidence = read(old/"judgment.json")["evidence"]
            assert a["inputs_sha256"] == digest([context,state,day])
            assert j["research_sha256"] == digest(raw)
            prediction, _ = prediction_from_research(raw)
            prediction["assessment"] = a["displayed"]
            evidence = {**evidence, "protocol":"owned-quotes-v1"}
            assert j["evidence_judgment_sha256"] == digest(evidence)
            assert j["score"] == score_case(ref,prediction,{**evidence,"verdict":j["result"]})
            receipts[panel][cid] = [a["displayed"]["label"], j["result"]["label"]]
            totals[j["result"]["label"]] += 1
            provider_calls.extend(a["calls"]+j["calls"])
    assert sum(totals.values()) == 17
    assert len({e["request_id"] for e in provider_calls}) == len(provider_calls)
    write(ROOT/f"{v}-integrity.json",{"runtime":runtimes,"rubric_sha256":rubric_digest(),"verified":17,
          "grade_totals":dict(totals),"recorded_usage":costs(provider_calls),"panels":receipts})
    print(v,dict(totals),costs(provider_calls))


if __name__ == "__main__":
    main()
