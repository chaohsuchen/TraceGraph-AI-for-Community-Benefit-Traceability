"""Classify additional URLs against already observed URLs by frozen quotation and contemporaneous byte/text identity."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote
from loss_audit import HERE, PROBE, capture, put, read, source_inventory
from produce import load

def fingerprint(url):
    page,status=load(url)
    return {"status":status,"characters":len(page),"sha256":sha256(page.encode()).hexdigest() if page else None},page

def basename(url):
    return unquote(urlsplit(url).path).rstrip("/").split("/")[-1].lower()

def host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")

def inspect(cid, prior):
    folder=prior/"runs/candidate"/cid
    d=HERE/"results/discover"/cid
    dest=HERE/"results/source-audit"/(cid+".json")
    if dest.is_file():return
    prod=read(d/"production.json")
    sources=source_inventory(read(folder/"research-events.json"),read(folder/"research.json"))
    old_capture=capture(read(folder/"research-events.json"))
    cited={x["url"] for x in prod["additions"]}
    examined={}
    for u in dict.fromkeys(v["url"] for v in prod["documents"] if v["url"] in cited):
        result,page=fingerprint(u)
        observed=next(v for v in prod["documents"] if v["url"]==u)
        unchanged=result["sha256"] is not None and result["sha256"]==observed["sha256"]
        matches=[]; old_checks=[]
        for old in sources:
            # Do not fetch unrelated hosts just to infer editorial independence.
            link_hints=bool(basename(old) and basename(u) and (basename(old)==basename(u)))
            comparable=host(old)==host(u) or link_hints
            if comparable:
                old_info,_=fingerprint(old)
                old_checks.append({"url":old,**old_info})
                if old_info["sha256"] and old_info["sha256"]==observed["sha256"]:
                    matches.append(old)
        overlap=[{"url":r["url"],"quote":r["excerpt"]} for r in old_capture.values()
                 if len(r["excerpt"].split())>=16 and page and r["excerpt"] in page] if unchanged else []
        if matches:typ="same_extracted_document"
        elif overlap:typ="repeats_previous_excerpt"
        elif any(host(u)==host(o) for o in sources):typ="same_host_distinct_or_unresolved_document"
        else:typ="different_host_independence_unestablished"
        examined[u]={"relationship":typ,"identical_content_urls":matches,"old_passages_repeated":overlap,
                     "snapshot_consistent":unchanged,"original_status":result,"same_host_or_filename_compared":old_checks}
    put(dest,{"case":cid,"selected_distinct_urls":len(cited),"review":examined})
if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--prior",type=Path,required=True)
    parser.add_argument("--cases",nargs="+",default=PROBE)
    args=parser.parse_args()
    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(lambda c:inspect(c,args.prior),args.cases))

