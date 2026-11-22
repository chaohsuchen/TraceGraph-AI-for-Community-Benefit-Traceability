"""Score blind output with the original v4 judge. Reuse a score only for an identical response."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import shutil
import sys
from loss_audit import HERE, PROBE, put, read

def score(arm, cid, opt):
    sys.path.insert(0,str(opt.judge.resolve()))
    sys.path.insert(0,str(opt.prior.resolve()))
    import run
    from tracegraph import nli_eval
    from tracegraph.nli_judge import rubric_digest
    assert rubric_digest()==read(opt.prior/"input-audit.json")["rubric_sha256"]
    dest=HERE/"results"/arm/cid
    src=opt.prior/"runs/candidate"/cid
    res=read(dest/"research.json")
    base=read(src/"research.json")
    shutil.copyfile(src/"reference.json",dest/"reference.json")
    if res==base:
        for fn in ("score.json","report.md"):
            shutil.copyfile(src/fn,dest/fn)
        put(dest/"score-reuse.json",{"original_sha256":nli_eval._digest(base),
            "original_judgment_sha256":nli_eval._digest(read(src/"judgment.json")),"calls":[]})
        return cid,arm,"unchanged"
    # Use the exact same research-event capture for partial tables. The formal
    # evidence and verdict stages compare the new user-visible content only.
    if base["outcome"]=="PARTIAL_RESEARCH" and not (dest/"research-events.json").is_file():
        shutil.copyfile(src/"research-events.json",dest/"research-events.json")
    if (dest/"score.json").is_file():
        nli_eval._score(dest,dest)
        return cid,arm,"done"
    options=argparse.Namespace(key_file=opt.key_file,history_dir=opt.prior/"runs/candidate")
    stamp=HERE/"results"/arm/"_ledger"/cid/"judge.json"
    if not stamp.exists():
        put(stamp,[{"phase":"judge","status":"RUNNING","started_at":run.now(),"prediction_sha256":nli_eval._digest(res)}])
    try:
        run.judge(options,dest)
    except (ValueError,RuntimeError):
        import repair
        options=argparse.Namespace(runs_root=HERE/"results",arm=arm,key_file=opt.key_file,runtime=opt.judge)
        repair.repair(options,cid)
    ledger=read(stamp)
    ledger[0].update(status="SCORED",finished_at=run.now())
    put(stamp,ledger)
    return cid,arm,"judged"

def main():
    p=argparse.ArgumentParser()
    for name in ("prior","judge","key-file"):p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--cases",nargs="+",default=PROBE)
    p.add_argument("--arms",nargs="+",default=("hidden","reread"))
    a=p.parse_args()
    with ProcessPoolExecutor(max_workers=4) as ex:
        tasks=[ex.submit(score,arm,c,a) for c in a.cases for arm in a.arms
               if (HERE/"results"/arm/c/"research.json").is_file()]
        for t in tasks:print(t.result(),flush=True)
if __name__=="__main__":main()

