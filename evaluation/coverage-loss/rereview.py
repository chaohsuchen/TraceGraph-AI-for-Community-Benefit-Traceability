"""Duplicate the v4 evidence pass, with identical inputs, for prespecified identity-sensitive cases."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from loss_audit import HERE, put, read

def run_pair(a, arm, case):
    from tracegraph import nli_eval, nli_judge, nli_score
    from tracegraph.nli_packet import prediction_from_research
    from tracegraph.providers import _structured
    from tracegraph.openai_provider import OpenAIClient
    from tracegraph.runtime import read_credential
    original=a.prior/"runs/candidate"/case
    folder=original if arm=="control" else HERE/"results"/arm/case
    target=HERE/"results/reviews"/arm/(case+".json")
    reference,result,passages=nli_eval._inputs(folder)
    pred,_=prediction_from_research(result)
    formal=read(folder/"judgment.json")["judgment"]
    fingerprint=nli_eval._digest(nli_judge._evidence_input(reference,pred))
    saved=read(target) if target.exists() else {"input_sha256":fingerprint,"calls":[]}
    assert fingerprint==saved["input_sha256"]
    if saved.get("evidence"):return
    def emit(event):
        saved["calls"].append(event);put(target,saved)
    client=OpenAIClient(api_key=read_credential(a.key_file,provider="openai"),model="gpt-5.4",allow_web_search=False,emit=emit)
    correction=None
    for turn in range(3):
        revised=nli_judge.judge_evidence(client,reference,pred,correction=correction)
        try: nli_score.validate_evidence(reference,pred,revised,require_quote_checks=True)
        except ValueError as exc:
            correction=str(exc)
            saved.setdefault("invalid",[]).append(correction)
            if turn==2: raise
        else:
            saved.update(evidence=revised,original_identity=formal["identity"]["label"],repeated_identity=revised["identity"]["label"])
            break
    put(target,saved)
if __name__=="__main__":
    p=argparse.ArgumentParser()
    for name in ("prior","judge","key-file"):p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args()
    sys.path.insert(0,str(a.judge));from tracegraph.nli_judge import rubric_digest
    assert rubric_digest()==read(a.prior/"input-audit.json")["rubric_sha256"]
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs=[ex.submit(run_pair,a,arm,"TG-GOLD-"+case) for arm in ("control","discover") for case in ("A034","H003","H010")]
        for f in fs:f.result()

