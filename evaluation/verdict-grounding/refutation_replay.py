"""Exercise the conditional performance recovery plus two explicit synthetic controls."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import sys
from unittest.mock import patch
from zipfile import ZipFile

from display_replay import inputs
from replay import ROOT, MODEL, assess, assessor_pin, digest, grade, read, source_dir, synthetic_inputs, write
from summarize import costs


ARM = "rejected-proof"
FINAL = "final-proof"
MAIN = "PUB-ELIZABETH"
FIXTURES = {key:read(ROOT/"fixtures"/path) for key,path in
            (("SYN-FIVE-ROUTES","five-routes.json"),("SYN-REVISED-DEPOT","revised-depot.json"))}


class StudyDate(datetime):
    @classmethod
    def now(cls, tz=None):
        value=cls.fromisoformat("2026-11-22T12:00:00+00:00")
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)


def receipt(args, case):
    from tracegraph.openai_provider import OpenAIClient
    from tracegraph.providers import ClaudeReconciliationJudge
    from tracegraph.reconcile import EvidenceReconciler
    from tracegraph.runtime import read_credential
    if case in FIXTURES:
        return assess(args,case,None,frozen_inputs=synthetic_inputs(FIXTURES[case]))
    assert case==MAIN
    source=read(ROOT/"runs/v7"/case/"assessment.json")
    context,state,day=inputs(args,"v7",case)
    assert source["inputs_sha256"] == digest([context,state,day])
    dest=ROOT/"runs"/ARM/case/"assessment.json"
    if dest.exists():
        raise ValueError("Preserve existing assessment.")
    saved={"case":case,"model":MODEL,"as_of":day,"source_trace_sha256":digest(source["trace"]),
           "inputs_sha256":source["inputs_sha256"],"runtime":assessor_pin(args.runtime),"calls":[]}
    def emit(e):
        assert not e.get("search_enabled") and not e.get("web_search_ids")
        saved["calls"].append(e)
        write(dest,saved)
    model=ClaudeReconciliationJudge(OpenAIClient(api_key=read_credential(args.key_file,provider="openai"),
                                        model=MODEL,allow_web_search=False,emit=emit))
    assert source["trace"]["label"]=="REFUTED" and len(source["trace"]["refutation"])==1
    assert source["displayed"]["label"]=="UNVERIFIED"
    engine=EvidenceReconciler(model,verify_refutation=lambda *_:False,verify_conflicts=lambda *_:None,
                              recover_delivery=model.recover_delivery)
    trace=deepcopy(source["trace"])
    write(dest,saved)
    try:
        result=engine._check_assessment(context,state,trace)
        assert "rejected_refutation_review" in trace
        saved.update(status="COMPLETED",trace=trace,displayed={**result,"verification_date":day})
    except Exception as error:
        saved.update(status="ERROR",error=str(error)[:300])
        raise
    finally:
        write(dest,saved)
    return result["label"]


def rerender(args,case):
    from tracegraph.reconcile import EvidenceReconciler
    assert case in (MAIN,"SYN-REVISED-DEPOT")
    prior=read(ROOT/"runs"/ARM/case/"assessment.json")
    trace=deepcopy(prior["trace"])
    assert trace.get("rejected_refutation_review",{}).get("status")=="COMPLETED"
    ctx,state,day=inputs(args,"v7",case) if case==MAIN else synthetic_inputs(FIXTURES[case])
    assert prior["inputs_sha256"]==digest([ctx,state,day])
    def no_repeat(*_):
        raise AssertionError("Rechecking an already recorded semantic decision is out of scope.")
    engine=EvidenceReconciler(None,verify_refutation=lambda *_:False,verify_conflicts=no_repeat,recover_delivery=no_repeat)
    current={**engine._check_assessment(ctx,state,trace),"verification_date":day}
    assert trace==prior["trace"]
    write(ROOT/"runs"/FINAL/case/"assessment.json",
          {"case":case,"model":MODEL,"status":"COMPLETED","runtime":assessor_pin(args.runtime),
           "source_trace_sha256":digest(trace),"inputs_sha256":prior["inputs_sha256"],"calls":[],"displayed":current})
    print(case,current["label"],"changed",current!=prior["displayed"])


def stable(args):
    from tracegraph.reconcile import EvidenceReconciler
    manifest=read(ROOT/"v7-display-replay.json")
    def untouched(*_):
        raise AssertionError("Previously unchanged trace entered the new semantic path.")
    rec=EvidenceReconciler(None,verify_refutation=untouched,verify_conflicts=untouched,recover_delivery=untouched)
    retained=[]
    for e in manifest["entries"]:
        if e["case"]==MAIN:
            assert e["panel"]=="v7" and e["changed"]
            continue
        source=ROOT/"runs"/e["panel"]/e["case"]
        a,j=read(source/"assessment.json"),read(source/"judge.json")
        ctx,state,day=inputs(args,e["panel"],e["case"])
        assert digest([ctx,state,day])==a["inputs_sha256"]
        current=rec._check_assessment(ctx,state,deepcopy(a["trace"]))
        assert digest({**current,"verification_date":day})==e["display_sha256"]==j["displayed_sha256"]
        retained.append({**e,"grade":j["result"]["label"]})
    assert len(retained)==17
    runtime=assessor_pin(args.runtime)
    deps={**runtime,"runtime.py":sha256((args.runtime/"tracegraph/runtime.py").read_bytes()).hexdigest()}
    overlay="scored-final-proof-overlay.zip" if args.final else "scored-refutation-overlay.zip"
    with ZipFile(ROOT/overlay) as over, ZipFile(args.study/"runtime-candidate.zip") as base:
        for name,h in deps.items():
            path="tracegraph/"+name
            assert sha256((over if path in over.namelist() else base).read(path)).hexdigest()==h
    write(ROOT/(args.arm+"-stable.json" if args.final else "refutation-stable.json"),{"runtime":runtime,"modules":deps,"cases":retained})
    print(len(retained),"unchanged including H017")


def audit(args):
    from tracegraph.nli_judge import JUDGE_VERSION,rubric_digest
    from tracegraph.nli_packet import prediction_from_research
    from tracegraph.nli_score import score_case
    retained=read(ROOT/(args.arm+"-stable.json" if args.final else "refutation-stable.json"))
    totals=Counter(x["grade"] for x in retained["cases"] if x["panel"]!="integration-v7")
    new, calls = {}, []
    for case in (MAIN,*FIXTURES):
        loc=ROOT/"runs"/args.arm/case
        a=read(loc/"assessment.json")
        assert a["runtime"]==retained["runtime"]
        calls.extend(a["calls"])
        if a["status"]=="ERROR":
            assert case in FIXTURES and not (loc/"judge.json").exists()
            new[case]=["TECHNICAL_ERROR",a["error"]]
            continue
        assert a["status"]=="COMPLETED"
        baseline=ROOT/"runs/v7-display"/MAIN if case==MAIN else ROOT/"runs"/ARM/case
        reused = (case==MAIN or (args.final and case=="SYN-REVISED-DEPOT")) and a["displayed"]==read(baseline/"assessment.json")["displayed"]
        if reused:
            assert not (loc/"judge.json").exists()
        j=read((baseline if reused else loc)/"judge.json")
        assert j["version"]==JUDGE_VERSION and j["rubric_sha256"]==rubric_digest()
        assert j["displayed_sha256"]==digest(a["displayed"])
        if case in FIXTURES:
            fixture=FIXTURES[case]
            ctx,state,date=synthetic_inputs(fixture)
            ref={"case_id":case,"query":fixture["query"],"conditions":fixture["evaluator_only"]["conditions"],
                 "facts":fixture["evaluator_only"]["facts"]}
            raw={"query":fixture["query"],"outcome":"RESOLVED","graph":{**state,"organization":{"name":fixture["organization"]},
                 "commitment":ctx["commitment"],"assessment":a["displayed"]}}
            evidence=j["evidence"]
            assert a["inputs_sha256"]==digest([ctx,state,date])
        else:
            folder=source_dir(args.study,case)
            ref,raw,saved=(read(folder/name) for name in ("reference.json","research.json","judgment.json"))
            evidence=saved["evidence"]
            original=read((ROOT/"runs"/ARM if args.final else ROOT/"runs/v7")/case/"assessment.json")
            assert original["inputs_sha256"]==a["inputs_sha256"] and digest(original["trace"])==a["source_trace_sha256"]
            totals[j["result"]["label"]]+=1
        evidence={**evidence,"protocol":"owned-quotes-v1"}
        assert j["evidence_judgment_sha256"]==digest(evidence) and j["research_sha256"]==digest(raw)
        prediction,_=prediction_from_research(raw)
        prediction["assessment"]=a["displayed"]
        assert j["score"]==score_case(ref,prediction,{**evidence,"verdict":j["result"]})
        new[case]=[a["displayed"]["label"],j["result"]["label"]]
        if not reused:
            calls.extend(j["calls"])
    assert sum(totals.values())==17 and len({c["request_id"] for c in calls})==len(calls)
    outputs={"formal_grades":dict(totals),"h017":next(x["grade"] for x in retained["cases"] if x["panel"]=="integration-v7"),
             "new_outputs":new,"additional_usage":costs(calls),"runtime":retained["modules"]}
    write(ROOT/(args.arm+"-integrity.json" if args.final else "refutation-integrity.json"),outputs)
    print(outputs)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase",choices=("stable","assess","rerender","judge","repair-depot-judge","audit"))
    for name in ("study","runtime","coverage"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--case",choices=(MAIN,*FIXTURES))
    p.add_argument("--key-file",type=Path)
    p.add_argument("--final",action="store_true")
    args=p.parse_args()
    args.as_of="2026-11-22"
    args.arm=FINAL if args.final else ARM
    sys.path.insert(0,str(args.runtime.resolve()))
    if args.phase=="stable":
        stable(args)
    elif args.phase=="audit":
        audit(args)
    elif args.phase=="assess":
        assert not args.final or args.case=="SYN-FIVE-ROUTES"
        with patch("tracegraph.providers.datetime",StudyDate):
            print(args.case,receipt(args,args.case))
    elif args.phase=="rerender":
        assert args.final
        rerender(args,args.case)
    elif (args.case==MAIN and read(ROOT/"runs"/args.arm/MAIN/"assessment.json")["displayed"]
          == read(ROOT/"runs/v7-display"/MAIN/"assessment.json")["displayed"]):
        print("Unchanged display; retain original blind grade.")
    elif (args.final and args.case=="SYN-REVISED-DEPOT"
          and read(ROOT/"runs"/FINAL/args.case/"assessment.json")["displayed"]==read(ROOT/"runs"/ARM/args.case/"assessment.json")["displayed"]):
        print("Unchanged synthetic display; retain original blind grade.")
    elif args.phase=="repair-depot-judge":
        assert args.case=="SYN-REVISED-DEPOT"
        print(args.case,grade(args,args.case,source_dir(args.study,args.case),fixture=FIXTURES[args.case],repair_evidence_format=True))
    else:
        print(args.case,grade(args,args.case,source_dir(args.study,args.case),fixture=FIXTURES.get(args.case)))
