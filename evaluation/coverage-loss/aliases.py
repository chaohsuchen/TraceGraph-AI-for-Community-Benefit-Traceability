"""Check redirects, exact PDF bytes and direct attachment links for new evidence URLs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin,urldefrag,urlsplit
import sys
from loss_audit import HERE,read,put
sys.path.insert(0,str(HERE.parents[1]))
from tracegraph.public_excerpt import _request
class Links(HTMLParser):
    def __init__(self):super().__init__();self.href=[]
    def handle_starttag(self,tag,attrs):
        item=dict(attrs)
        if tag=="a" and item.get("href"):self.href.append(item["href"])
def grab(url):
    original=url; redirects=[];deadline=monotonic()+18
    try:
        for _ in range(4):
            location,headers,body=_request(url,deadline)
            if location:
                url=urljoin(url,location);redirects.append(url);continue
            if body is None:break
            result={"url":original,"final":url,"redirects":redirects,"length":len(body),"body_sha256":sha256(body).hexdigest(),"urls":[]}
            if headers.get_content_type()=="text/html":
                p=Links();p.feed(body.decode("utf8",errors="replace"))
                result["urls"]=list(dict.fromkeys(urldefrag(urljoin(url,v)).url for v in p.href))
            return result
    except (OSError,ValueError,RuntimeError) as e:
        return {"url":original,"failure":type(e).__name__}
    return {"url":original,"failure":"no_readable_response"}
def main():
    p=argparse.ArgumentParser();p.add_argument("--prior",type=Path,required=True)
    args=p.parse_args()
    # Pair only the sources attached to actual gains credited with a new citation,
    # plus all corresponding original URLs on the same host.
    checkset={}
    from loss_audit import source_inventory
    from tracegraph.nli_packet import prediction_from_research
    for d in (HERE/"results/discover").glob("TG-*"):
        if not (d/"judgment.json").exists() or not (d/"score.json").exists():continue
        old=args.prior/"runs/candidate"/d.name
        before=read(old/"research.json");after=read(d/"research.json")
        prev=prediction_from_research(before)[0];pred=prediction_from_research(after)[0]
        known={s["id"] for f in (prev or {}).get("findings",[]) for s in f["supports"]}
        index={s["id"]:s["source"]["url"] for f in pred["findings"] for s in f["supports"] if s["id"] not in known}
        gains=set(read(d/"score.json")["credited_reference_ids"])-set(read(old/"score.json")["credited_reference_ids"])
        prior=list(source_inventory(read(old/"research-events.json"),before))
        for g in read(d/"judgment.json")["judgment"]["coverage"]:
            if g["gold_id"] not in gains:continue
            for sid in g["citation_ids"]:
                if sid in index:
                    nid=index[sid];key=(d.name,nid)
                    checkset[key]=[v for v in prior if urlsplit(v).hostname==urlsplit(nid).hostname]
    urls=set(u for _,u in checkset)|{v for items in checkset.values() for v in items}
    with ThreadPoolExecutor(max_workers=4) as ex:
        records=dict(zip(sorted(urls),ex.map(grab,sorted(urls))))
    rows=[]
    for (cid,new),olds in checkset.items():
        nr=records[new];matches=[]
        for old in olds:
            rec=records[old]
            if nr.get("body_sha256") and nr["body_sha256"]==rec.get("body_sha256"):kind="identical_response_bytes"
            elif new in rec.get("urls",[]) or nr.get("final") and nr["final"] in rec.get("urls",[]):kind="old_page_links_to_new_attachment"
            elif nr.get("final") and nr["final"]==rec.get("final"):kind="same_final_url"
            else:continue
            matches.append({"url":old,"relationship":kind})
        rows.append({"case":cid,"new":new,"same_host_old_urls":olds,"matches":matches})
    # A page can contain thousands of irrelevant links. Persist bytes and direct
    # attachment matches only; all downstream analysis uses these fields.
    for rec in records.values():rec.pop("urls",None)
    put(HERE/"results/download-equivalence.json",{"records":records,"pairs":rows})
if __name__=="__main__":main()

