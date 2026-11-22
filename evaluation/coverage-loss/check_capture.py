"""Independent sensitivity read of the first capture judge's negative decisions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from loss_audit import HERE, put, read

def inspect(case, args, ledger):
    from tracegraph import nli_judge,nli_score,nli_eval
    from tracegraph.openai_provider import OpenAIClient
    from tracegraph.runtime import read_credential
    folder=args.prior/"runs/candidate"/case; ref=read(folder/"reference.json")
    first=read(HERE/"results/capture"/(case+".json"))
    selected={r["id"] for r in ledger if r["case"]==case and r["capture_label"] not in ("ENTAILMENT","NO_CAPTURE") and (r["stage"].startswith("reference_url") or r["stage"]=="credited")}
    original=first["passages"]; ordered=list(reversed(list(original.values())))
    passages={f"p{i}":p for i,p in enumerate(ordered,1)}
    filtered={"query":ref["query"],"facts":[f for f in ref["facts"] if f["id"] in selected]}
    fingerprint=nli_eval._digest([filtered["query"],filtered["facts"],passages,nli_judge.rubric_digest()])
    target=HERE/"results/sensitivity"/(case+".json")
    if target.is_file():
        log=read(target)
        assert log["fingerprint"]==fingerprint
        if "decisions" in log:return
    else: log={"fingerprint":fingerprint,"reversed_original_ids":list(reversed(list(original))),"calls":[]}
    if not selected or not passages:
        log["decisions"]=[]
    else:
        def emit(event):
            log["calls"].append(event);put(target,log)
        client=OpenAIClient(api_key=read_credential(args.key_file,provider="openai"),model="gpt-5.4",allow_web_search=False,emit=emit)
        error=None
        for i in range(2):
            reply=nli_judge.judge_capture(client,filtered,{"outcome":read(folder/"research.json")["outcome"]},passages,correction=error)
            try:nli_score.score_capture(filtered,passages,reply)
            except ValueError as e:
                error=str(e)
                if i:raise
            else:
                log["decisions"]=reply["facts"];break
    put(target,log)
if __name__=="__main__":
    p=argparse.ArgumentParser()
    for k in ("prior","judge","key-file"):p.add_argument("--"+k,type=Path,required=True)
    a=p.parse_args()
    sys.path.insert(0,str(a.judge));from tracegraph.nli_judge import rubric_digest
    assert rubric_digest()==read(a.prior/"input-audit.json")["rubric_sha256"]
    ledger=read(HERE/"results/loss-ledger.json")
    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(lambda c:inspect(c,a,ledger), sorted({r["case"] for r in ledger})))

