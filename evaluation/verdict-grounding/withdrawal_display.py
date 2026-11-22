"""Paired check of the display after official withdrawal, with all upstream evidence frozen."""
import argparse
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
from unittest.mock import patch

from refutation_replay import FIXTURES
from replay import ROOT, assessor_pin, digest, grade, read, synthetic_inputs, write
from summarize import costs


CASE = "SYN-FIVE-ROUTES"
ARM = "withdrawal-display"
PRIOR = ROOT/"runs/final-proof"/CASE
DEST = ROOT/"runs"/ARM/CASE


def run(args):
    fixture=FIXTURES[CASE]
    ctx,state,day=synthetic_inputs(fixture)
    prior=read(PRIOR/"assessment.json")
    prior_judge=read(PRIOR/"judge.json")
    assert prior["inputs_sha256"]==digest([ctx,state,day]) and prior["displayed"]["label"]=="REFUTED"
    if args.phase=="render":
        from tracegraph import providers
        from tracegraph.openai_provider import OpenAIClient
        from tracegraph.reconcile import EvidenceReconciler
        from tracegraph.runtime import read_credential
        if (DEST/"assessment.json").exists():
            raise ValueError("Preserve the independent acceptance recheck.")
        trace=deepcopy(prior["trace"])
        proof,=trace["refutation"]
        assert proof["basis"]=="OFFICIAL_WITHDRAWAL"
        notice=state["findings"][1]["supports"][0]
        assert proof["quote"]==notice["excerpt"] and proof["quote"] in prior["displayed"]["reasoning"]
        log={"case":CASE,"source_trace_sha256":digest(prior["trace"]),"upstream_proof":proof,"calls":[],"verification":[]}
        def emit(event):
            assert not event.get("search_enabled") and not event.get("web_search_ids")
            log["calls"].append(event)
            write(DEST/"assessment.json",log)
        judge=providers.ClaudeReconciliationJudge(OpenAIClient(api_key=read_credential(args.key_file,provider="openai"),
                                model="gpt-5.4",allow_web_search=False,emit=emit))
        original=providers._structured
        def record(*params,**kwargs):
            response=original(*params,**kwargs)
            log["verification"].append(response)
            write(DEST/"assessment.json",log)
            return response
        def accepted(_ctx,support,basis,quote):
            assert support==notice and quote==proof["quote"] and basis==proof["basis"]
            return judge.verify_refutation(_ctx,support,basis,quote)
        def untouched(*_):
            raise AssertionError("Presentation-only change requested an unrecorded semantic judgment.")
        verifier=EvidenceReconciler(None,verify_refutation=accepted,verify_conflicts=untouched,recover_delivery=untouched)
        with patch.object(providers,"_structured",record):
            displayed={**verifier._check_assessment(ctx,state,trace),"verification_date":day}
        assert any(x.get("basis")=="OFFICIAL_WITHDRAWAL" for x in log["verification"])
        assert any(x.get("directly_refutes") is True for x in log["verification"])
        assert trace["verified_refutation"]["basis"]=="OFFICIAL_WITHDRAWAL"
        assert displayed["label"]==prior["displayed"]["label"]
        out={**log,"status":"COMPLETED","inputs_sha256":prior["inputs_sha256"],"runtime":assessor_pin(args.runtime),
             "runtime_py":sha256((args.runtime/"tracegraph/runtime.py").read_bytes()).hexdigest(),
             "verified_refutation":trace["verified_refutation"],"displayed":displayed}
        write(DEST/"assessment.json",out)
        print("Display changed",displayed!=prior["displayed"])
        return
    a=read(DEST/"assessment.json")
    assert a["source_trace_sha256"]==digest(prior["trace"]) and a["inputs_sha256"]==prior["inputs_sha256"]
    assert a["displayed"]!=prior["displayed"]
    if args.phase=="judge":
        print(grade(args,CASE,None,fixture=fixture,frozen_synthetic_evidence=prior_judge["evidence"]))
        return
    from tracegraph.nli_judge import JUDGE_VERSION,rubric_digest
    from tracegraph.nli_packet import prediction_from_research
    from tracegraph.nli_score import score_case
    j=read(DEST/"judge.json")
    assert j["version"]==JUDGE_VERSION and j["rubric_sha256"]==rubric_digest()
    assert j["evidence"]==prior_judge["evidence"] and j["reused_evidence_sha256"]==digest(j["evidence"])
    ref={"case_id":CASE,"query":fixture["query"],"conditions":fixture["evaluator_only"]["conditions"],"facts":fixture["evaluator_only"]["facts"]}
    raw={"query":fixture["query"],"outcome":"RESOLVED","graph":{**state,"organization":{"name":fixture["organization"]},
         "commitment":ctx["commitment"],"assessment":a["displayed"]}}
    prediction,_=prediction_from_research(raw)
    assert j["displayed_sha256"]==digest(a["displayed"]) and j["research_sha256"]==digest(raw)
    assert j["score"]==score_case(ref,prediction,{**j["evidence"],"protocol":"owned-quotes-v1","verdict":j["result"]})
    previous=read(ROOT/"final-proof-integrity.json")
    totals={}
    for f in (ROOT/"runs").glob("*/*/*.json"):
        totals.setdefault(f.name,[]).extend(read(f).get("calls",[]))
    all_calls=[c for rows in totals.values() for c in rows]
    assert len(all_calls)==len({c["request_id"] for c in all_calls})
    out={"formal_grades":previous["formal_grades"],"h017":previous["h017"],
         "additional_diagnostics":{CASE:[a["displayed"]["label"],j["result"]["label"]],
                                   "SYN-REVISED-DEPOT":previous["new_outputs"]["SYN-REVISED-DEPOT"]},
         "previous_diagnostic_grade":prior_judge["result"]["label"],"additional_usage":costs(a["calls"]+j["calls"]),
         "all_recorded_usage":costs(all_calls),"stage_usage":{p:costs(v) for p,v in totals.items() if v}}
    write(ROOT/"withdrawal-display-integrity.json",out)
    print(out)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase",choices=("render","judge","audit"))
    p.add_argument("--runtime",type=Path,required=True)
    p.add_argument("--key-file",type=Path)
    args=p.parse_args()
    args.arm=ARM
    sys.path.insert(0,str(args.runtime.resolve()))
    run(args)
