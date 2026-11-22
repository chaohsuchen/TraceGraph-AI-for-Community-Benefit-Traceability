"""Summarize strict matched scores, contributing citations, metered calls and source-stage losses."""
import argparse
from collections import Counter
import csv
from itertools import product
from pathlib import Path
import sys
from loss_audit import HERE, PROBE, put, read
sys.path.insert(0,str(HERE.parents[1]))
from tracegraph.nli_packet import prediction_from_research

def ratio(n,d):return n/d if d else None

def evidence_judgment(folder,baseline):
    p=folder/"judgment.json"
    return read(p if p.is_file() else baseline/"judgment.json")["judgment"]

def summarize(a, cases, tag, arms):
    sys.path.insert(0,str(a.prior.parent/"golden22"))
    from summarize import usage
    rows=[]
    outputs={}
    for arm in ("control",*arms):
        series=[]
        tool=[]
        grading=[]
        for cid in cases:
            orig=a.prior/"runs/candidate"/cid
            folder=orig if arm=="control" else HERE/"results"/arm/cid
            base=read(orig/"score.json"); cur=read(folder/"score.json")
            reference=read(orig/"reference.json")
            before=set(base["credited_reference_ids"]); after=set(cur["credited_reference_ids"])
            prev_dec={r["gold_id"]:r for r in evidence_judgment(orig,orig)["coverage"]}
            current_dec={r["gold_id"]:r for r in evidence_judgment(folder,orig)["coverage"]}
            prediction,_=prediction_from_research(read(orig/"research.json"))
            prev_ids={s["id"] for f in prediction["findings"] for s in f["supports"]} if prediction else set()
            types={}
            for gid in sorted(after-before):
                if prev_dec[gid]["label"]=="ENTAILMENT" and base["identity"]!="MATCH":kind="prior_fact_gated_by_identity"
                elif set(current_dec[gid]["citation_ids"])-prev_ids:kind="new_citation"
                else:kind="reclassified_using_only_original_citations"
                types[gid]=kind
            row={"case":cid,"arm":arm,"facts":len(reference["facts"]),"baseline":len(before),"hits":len(after),
                 "gained":sorted(after-before),"lost":sorted(before-after),"gains_by_mechanism":types,
                 "identity":cur["identity"],"underlying_identity":base["identity"],"verdict":cur["verdict"],
                 "support":cur["finding_support"]["numerator"],"claims":cur["finding_support"]["denominator"]}
            if arm!="control":
                production=read(folder/"production.json")
                calls=production["calls"]; tool.extend(calls)
                jc=read(folder/"judgment.json")["calls"] if (folder/"judgment.json").exists() else []
                grading.extend(jc)
                row.update(additions=len(production["additions"]),document_reads=len(production["documents"]),
                           readable=sum(d["status"]=="readable" for d in production["documents"]),
                           incremental_production_cost=round(usage(calls)["estimated_usd"],6))
            series.append(row)
        rows.extend(series)
        wins=sum(r["hits"]>r["baseline"] for r in series)
        losses=sum(r["hits"]<r["baseline"] for r in series)
        diffs=[r["hits"]-r["baseline"] for r in series if r["hits"]!=r["baseline"]]
        two_sided=sum(abs(sum(d*s for d,s in zip(diffs,signs)))>=abs(sum(diffs)) for signs in product((-1,1),repeat=len(diffs)))/2**len(diffs)
        doc_sum=sum(r.get("document_reads",0) for r in series)
        outputs[arm]={"cases":len(cases),"hits":sum(r["hits"] for r in series),"facts":sum(r["facts"] for r in series),
            "supported":sum(r["support"] for r in series),"claims":sum(r["claims"] for r in series),
            "overclaims":sum(r["verdict"]=="OVERCLAIM" for r in series),"verdicts":sum(r["verdict"] is not None for r in series),
            "identity_match":sum(r["identity"]=="MATCH" for r in series),"wins_ties_losses":[wins,len(series)-wins-losses,losses],
            "gain_mechanisms":dict(Counter(v for r in series for v in r["gains_by_mechanism"].values())),
            "lost":sum(len(r["lost"]) for r in series),"paired_sign_flip_two_sided_p":two_sided,
            "added_findings":sum(r.get("additions",0) for r in series),"cases_with_additions":sum(r.get("additions",0)>0 for r in series),
            "readable_docs":sum(r.get("readable",0) for r in series),"document_attempts":doc_sum,
            "incremental_production":dict(usage(tool)),"incremental_judge":dict(usage(grading))}
    capture_calls=[e for path in (HERE/"results/capture").glob("*.json") for e in read(path)["calls"]]
    saved={"case_ids":cases,"arms":outputs,"diagnostic_usage":dict(usage(capture_calls))}
    put(HERE/"results"/f"{tag}-summary.json",saved)
    put(HERE/"results"/f"{tag}-pairs.json",rows)
    with (HERE/"results"/f"{tag}-pairs.csv").open("w",newline="",encoding="utf-8-sig") as h:
        keys=list(dict.fromkeys(k for r in rows for k in r))
        wr=csv.DictWriter(h,fieldnames=keys);wr.writeheader();wr.writerows(rows)
    print(outputs)
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--prior",type=Path,required=True)
    p.add_argument("--cases",nargs="+",default=PROBE)
    p.add_argument("--arms",nargs="+",default=["hidden","reread","discover"])
    p.add_argument("--tag",default="probe")
    a=p.parse_args()
    summarize(a,a.cases,a.tag,a.arms)

