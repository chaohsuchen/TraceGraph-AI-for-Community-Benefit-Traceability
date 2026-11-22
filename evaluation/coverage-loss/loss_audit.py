"""Build a per-reference loss ledger with the frozen tool-free capture judge."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
PROBE = tuple("TG-GOLD-" + x for x in "A006 A034 A061 H003 H008 H010 H023 H030".split())

def read(p):
    return json.loads(p.read_text(encoding="utf8"))

def put(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf8")

def capture(events):
    all_rows = {}
    for event in events:
        if event.get("type") in {"research_capture","research_recovery_capture"}:
            for item in event["citations"].values():
                ident = (item["url"], item["excerpt"])
                all_rows.setdefault(ident, {k:item[k] for k in ("url","title","excerpt")})
    return {f"p{i}":v for i,v in enumerate(all_rows.values(),1)}

def document(url):
    if not url or not url.startswith(("https://","http://")):
        return None
    u = urlsplit(url)
    return (u.netloc.lower(),u.path.rstrip("/"),u.query)

def source_inventory(events, result):
    inventory = {}
    def add(url, title, origin):
        if document(url):
            slot = inventory.setdefault(url, {"title": title or url,"origins":[]})
            if origin not in slot["origins"]: slot["origins"].append(origin)
            if slot["title"] == url and title: slot["title"] = title
    for event in events:
        if event.get("type") == "source_validation":
            add(event.get("url"),None,event.get("status","validation"))
        if event.get("type") in {"research_capture","research_recovery_capture"}:
            for s in event["citations"].values(): add(s["url"],s["title"],"captured")
    for key in ("facts","suggestions","unmatched_records"):
        for row in (result.get("job_result") or {}).get(key,[]):
            add(row.get("source_url"),row.get("source_title") or row.get("title"),key)
    return inventory

def audit_case(folder, key):
    from tracegraph import nli_eval, nli_judge, nli_score
    from tracegraph.openai_provider import OpenAIClient
    from tracegraph.runtime import read_credential
    ref,result,old_passages = nli_eval._inputs(folder)
    ev = read(folder/"research-events.json")
    passages = capture(ev)
    judge = read(folder/"judgment.json")
    target = HERE/"results/capture"/(folder.name+".json")
    fingerprint = nli_eval._digest([ref["query"],ref["facts"],passages,nli_judge.rubric_digest()])
    if target.is_file():
        out = read(target)
        assert out["fingerprint"] == fingerprint
        if "decisions" in out: return
    else:
        out = {"fingerprint":fingerprint,"origin":None,"passages":passages,"calls":[]}
    if not passages:
        out.update(origin="empty",decisions=[])
    elif old_passages == passages and judge.get("capture_judgment"):
        out.update(origin="frozen",decisions=judge["capture_judgment"]["facts"])
    else:
        def emit(e):
            out["calls"].append(e)
            put(target,out)
        client = OpenAIClient(api_key=read_credential(key,provider="openai"),model="gpt-5.4",allow_web_search=False,emit=emit)
        correction = None
        for attempt in range(2):
            reply=nli_judge.judge_capture(client,ref,{"outcome":result["outcome"]},passages,correction=correction)
            try:
                nli_score.score_capture(ref,passages,reply)
            except ValueError as exc:
                correction=str(exc)
                if attempt: raise
            else:
                out.update(origin="new_tool_free_v4",decisions=reply["facts"])
                break
    put(target,out)

def classify(folder):
    ref = read(folder/"reference.json")
    research=read(folder/"research.json")
    score=read(folder/"score.json")
    judge=read(folder/"judgment.json")["judgment"]
    official=set(score["credited_reference_ids"])
    shown={v["gold_id"]:v for v in judge["coverage"]}
    capture_out=read(HERE/"results/capture"/(folder.name+".json"))
    known={v["gold_id"]:v for v in capture_out["decisions"]}
    inventory=source_inventory(read(folder/"research-events.json"),research)
    seen={document(url):value for url,value in inventory.items()}
    by_row={(s["sheet"],s["row"]):s for s in ref["sources"]}
    for fact in ref["facts"]:
        fid=fact["id"]
        src=[by_row.get((r["sheet"],r["row"]),{}) for r in fact["source_refs"]]
        reference_urls=[s["url"] for s in src if s.get("url") and document(s["url"])]
        matched=[url for url in reference_urls if document(url) in seen]
        retained=known.get(fid,{})
        if fid in official: stage="credited"
        elif shown[fid]["label"]=="ENTAILMENT" and score["identity"]!="MATCH": stage="visible_identity_gate"
        elif retained.get("label")=="ENTAILMENT": stage="captured_not_credited"
        elif matched: stage="reference_url_seen_without_entailed_capture"
        elif reference_urls: stage="reference_url_not_in_archive"
        else: stage="reference_url_unavailable"
        yield {"case":ref["case_id"],"id":fid,"fact":fact["text"],"stage":stage,"formal_identity":score["identity"],
               "formal_visible_label":shown[fid]["label"],"capture_label":retained.get("label","NO_CAPTURE"),
               "capture_reason":retained.get("reason"),"passage_ids":retained.get("citation_ids",[]),
               "matched_reference_urls":matched,"reference_urls":reference_urls,
               "related_document_observations":[seen[document(url)]["origins"] for url in matched]}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--prior",type=Path,required=True)
    ap.add_argument("--judge",type=Path,required=True)
    ap.add_argument("--key-file",type=Path,required=True)
    a=ap.parse_args()
    sys.path.insert(0,str(a.judge.resolve()))
    from tracegraph.nli_judge import rubric_digest
    assert rubric_digest()==read(a.prior/"input-audit.json")["rubric_sha256"]
    folders=sorted((a.prior/"runs/candidate").glob("TG-GOLD-*"))
    assert len(folders)==22
    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(lambda d:audit_case(d,a.key_file),folders))
    rows=[row for d in folders for row in classify(d)]
    totals=Counter(r["stage"] for r in rows)
    assert len(rows)==176 and totals["credited"]==39
    put(HERE/"results/loss-ledger.json",rows)
    put(HERE/"results/loss-counts.json",dict(totals))
    with (HERE/"results/loss-ledger.csv").open("w",newline="",encoding="utf-8-sig") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(dict(totals))
if __name__=="__main__": main()

