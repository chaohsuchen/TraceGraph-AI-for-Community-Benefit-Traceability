"""Replay the final production assessor on immutable public excerpts, then run the frozen verdict judge."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
CASES = ("TG-GOLD-H012", "PUB-COBALT", "TG-GOLD-H007", "PUB-DART", "TG-GOLD-H008", "PUB-ELIZABETH")
TRANSFER = ("TG-GOLD-A036", "TG-GOLD-A061", "TG-GOLD-H016", "TG-GOLD-H022")
REPEATS = ("TG-GOLD-H012", "PUB-COBALT", "PUB-DART")
SYNTHETIC = "SYN-SCOPE-NEG"
MODEL = "gpt-5.4"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256(raw).hexdigest()


def source_dir(study, case):
    return study / "runs" / ("holdout-candidate" if case.startswith("PUB-") else "candidate") / case


def assessor_inputs(folder):
    # Match the application's first discovery context. Scope anchors derive from discovery,
    # and owned excerpts are reconstructed in their original transient-ID order.
    from tracegraph.agent import _DISCOVERED_FIELDS
    from tracegraph.providers import _assessment_citations
    original = read(folder / "research.json")
    graph = original["graph"]
    trace = graph["assessment_trace"]
    extracts = [e["draft"] for e in read(folder / "research-events.json") if e.get("type") == "research_extracted"]
    assert len(extracts) == 1 and original["outcome"] == "RESOLVED"
    draft = extracts[0]
    context = {"commitment": {**{k: draft.get(k) for k in _DISCOVERED_FIELDS}, "title": draft["title"]},
               "organization": None, "requested_query": original["query"],
               "requested_criteria": list(trace["scope_anchors"].values())}
    findings = []
    by_id = {f["id"]: f for f in graph["findings"]}
    citations = {}
    for cid, citation in trace["citations"].items():
        fid = citation["finding_id"]
        assert len(citation["support_ids"]) == 1
        sid, = citation["support_ids"]
        support, = [s for s in by_id[fid]["supports"] if s["id"] == sid]
        if "retrieved_at" in citation and support["retrieved_at"] != citation["retrieved_at"]:
            assert datetime.fromisoformat(support["retrieved_at"]) == datetime.fromisoformat(citation["retrieved_at"])
            support = {**support, "retrieved_at": citation["retrieved_at"]}
        if not findings or findings[-1]["id"] != fid:
            findings.append({"id": fid, "claim": by_id[fid]["claim"], "supports": []})
        findings[-1]["supports"].append(support)
        citations[cid] = {k: v for k, v in citation.items() if k != "support_ids"}
    assert len(findings) == len(graph["findings"])
    snapshot = {"revision": 0, "findings": findings, "relations": graph["relations"]}
    assert _assessment_citations(snapshot) == citations, "Original source ownership/order changed."
    return context, snapshot, graph["assessment"]["verification_date"]


def assessor_pin(runtime):
    return {name: sha256((runtime / "tracegraph" / name).read_bytes()).hexdigest()
            for name in ("agent.py", "providers.py", "reconcile.py", "openai_provider.py")}


def synthetic_inputs():
    fixture = read(ROOT / "fixtures/scope-negative.json")
    findings = [{"id": "f"+p["id"], "claim": p["quote"], "supports": [{
        "id": "s"+p["id"], "source": {"url": p["url"], "title": p["title"], "citation":p["url"]},
        "excerpt": p["quote"], "locator": "Synthetic excerpt", "retrieved_at": fixture["date"]+"T00:00:00Z",
    }]} for p in fixture["passages"]]
    context = {"organization": None, "requested_query": fixture["query"], "requested_criteria": fixture["scope_hints"],
               "commitment": {"organization_name": fixture["organization"], "title": fixture["title"],
                              "description": fixture["description"], "original_promise": findings[0]["supports"][0]}}
    return context, {"revision": 0,"relations": [],"findings": findings}, fixture["date"]


def assess(args, cid, folder, *, frozen_inputs=None):
    from tracegraph.openai_provider import OpenAIClient
    from tracegraph.providers import ClaudeReconciliationJudge
    from tracegraph.reconcile import EvidenceReconciler
    from tracegraph.runtime import read_credential
    context, snapshot, day = frozen_inputs if frozen_inputs is not None else (
        synthetic_inputs() if cid == SYNTHETIC else assessor_inputs(folder))
    target = ROOT / "runs" / args.arm / cid / "assessment.json"
    if target.exists():
        raise ValueError(f"Retain existing attempt at {target.name} for {cid}.")
    receipt = {"case": cid, "model": MODEL, "runtime": assessor_pin(args.runtime),
               "inputs_sha256": digest([context, snapshot, day]), "as_of": day, "calls": []}
    write(target, receipt)

    def record_call(event):
        receipt["calls"].append(event)
        write(target, receipt)
        if event.get("search_enabled") or event.get("web_search_ids"):
            raise RuntimeError("Unexpected web call.")

    client = OpenAIClient(api_key=read_credential(args.key_file, provider="openai"),
                          model=MODEL, allow_web_search=False, emit=record_call)
    judge = ClaudeReconciliationJudge(client)
    reconciler = EvidenceReconciler(judge, assessor=judge.assess,
                                    verify_refutation=judge.verify_refutation, verify_conflicts=judge.verify_conflicts)
    try:
        with_frozen_date = args.as_of
        assert with_frozen_date == day
        value = judge.assess(context, snapshot)
        displayed = reconciler._check_assessment(context, snapshot, value)
        receipt.update(status="COMPLETED", trace=value, displayed={**displayed, "verification_date": day})
    except Exception as exc:
        receipt.update(status="ERROR", error_type=type(exc).__name__, error=str(exc)[:300])
        raise
    finally:
        write(target, receipt)
    return displayed["label"]


def grade(args, cid, folder):
    from tracegraph.nli_judge import JUDGE_VERSION, rubric_digest, judge_evidence, judge_verdict
    from tracegraph.nli_packet import prediction_from_research
    from tracegraph.nli_score import score_case, validate_evidence
    from tracegraph.openai_provider import OpenAIClient
    from tracegraph.runtime import read_credential
    local = ROOT / "runs" / args.arm / cid
    result = read(local / "assessment.json")
    assert result["status"] == "COMPLETED"
    if cid == SYNTHETIC:
        fixture = read(ROOT/"fixtures/scope-negative.json")
        context, snapshot, _ = synthetic_inputs()
        gold = {"case_id":cid, "query":fixture["query"], "conditions":fixture["evaluator_only"]["conditions"],
                "facts":[{"id":"G1","text":fixture["passages"][0]["quote"]}]}
        research = {"query":fixture["query"], "outcome":"RESOLVED",
                    "graph":{**snapshot, "organization":{"name":fixture["organization"]},
                             "commitment":context["commitment"], "assessment":result["displayed"]}}
        saved, prior = {}, None
    else:
        gold = read(folder / "reference.json")
        research = read(folder / "research.json")
        saved = read(folder / "judgment.json")
        assert saved["judge_version"] == JUDGE_VERSION and saved["rubric_sha256"] == rubric_digest()
        prior = saved["judgment"]["verdict"]
    prediction, _ = prediction_from_research(research)
    graph = deepcopy(prediction)
    graph["assessment"] = result["displayed"]
    target = local / "judge.json"
    if target.exists():
        raise ValueError(f"Retain existing judge attempt for {cid}.")
    receipt = {"case": cid, "model": MODEL, "version": JUDGE_VERSION, "rubric_sha256": rubric_digest(),
               "research_sha256": digest(research), "displayed_sha256": digest(result["displayed"]),
               "original": prior, "calls": []}
    write(target, receipt)

    def emit(event):
        receipt["calls"].append(event)
        write(target, receipt)
        assert not event.get("search_enabled") and not event.get("web_search_ids")

    client = OpenAIClient(api_key=read_credential(args.key_file, provider="openai"),
                          model=MODEL, allow_web_search=False, emit=emit)
    if cid == SYNTHETIC:
        correction = None
        for _ in range(2):
            observed = judge_evidence(client, gold, graph, correction=correction)
            try:
                validate_evidence(gold, graph, observed, require_quote_checks=True)
            except ValueError as exc:
                correction = str(exc)
                receipt.setdefault("evidence_rejections", []).append({"response":observed,"error":correction})
                write(target, receipt)
            else:
                receipt["evidence"] = observed
                saved["evidence"] = observed
                break
        else:
            raise ValueError(correction)
    evidence = {**saved["evidence"], "protocol": "owned-quotes-v1"}
    validate_evidence(gold, graph, evidence, require_quote_checks=True)
    receipt["evidence_judgment_sha256"] = digest(evidence)
    correction = None
    for _ in range(2):
        response = judge_verdict(client, gold, graph, evidence, correction=correction)
        try:
            score = score_case(gold, graph, {**evidence, "verdict": response})
        except ValueError as exc:
            correction = str(exc)
            receipt.setdefault("rejections", []).append({"response": response, "error": correction})
            write(target, receipt)
        else:
            receipt.update(result=response, score=score)
            write(target, receipt)
            return response["label"]
    raise ValueError(correction)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("audit", "assess", "judge"))
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--arm", choices=("control", "repaired", "repaired-v2", "repaired-v3", "transfer",
                                         "repeat-2", "repeat-3", "final-panels", "v4", "v4-transfer",
                                         "v4-r2", "v4-r3", "v4-negative", "v4-panels",
                                         "v5", "v5-transfer", "v5-r2", "v5-r3", "v5-negative", "v5-panels",
                                         "v6", "v6-transfer", "v6-r2", "v6-r3", "v6-negative", "v6-panels",
                                         "v7", "v7-transfer", "v7-r2", "v7-r3", "v7-negative", "v7-panels"), default="repaired")
    parser.add_argument("--cases", nargs="+", choices=(*CASES, *TRANSFER, SYNTHETIC))
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.arm.endswith("-panels") and args.cases:
        parser.error("Use individual arms for a specific subset.")
    cohorts = {"transfer":TRANSFER, "repeat-2":REPEATS, "repeat-3":REPEATS}
    for tag in ("v4","v5","v6","v7"):
        cohorts.update({tag+"-transfer":TRANSFER, tag+"-r2":REPEATS, tag+"-r3":REPEATS, tag+"-negative":(SYNTHETIC,)})
    cohort = cohorts.get(args.arm, CASES)
    args.cases = args.cases or cohort
    if set(args.cases) - set(cohort):
        parser.error("Case outside the prespecified cohort for this arm.")
    args.runtime = args.runtime.resolve()
    sys.path.insert(0, str(args.runtime))
    # Fixed clock covers all six recordings. The temporary patch is shared read-only by workers.
    args.as_of = "2026-11-22"

    class StudyClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat(args.as_of + "T12:00:00+00:00")
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    def run(item):
        arm, cid = item
        options = copy(args)
        options.arm = arm
        folder = source_dir(args.study, cid)
        if args.stage == "audit":
            context, snapshot, day = synthetic_inputs() if cid == SYNTHETIC else assessor_inputs(folder)
            outcome = f"{sum(len(f['supports']) for f in snapshot['findings'])} matching original citations, {digest([context,snapshot,day])}"
        else:
            outcome = (assess if args.stage == "assess" else grade)(options, cid, folder)
        print(arm, cid, args.stage, outcome, flush=True)

    if args.stage != "audit" and args.key_file is None:
        parser.error("Specify the local key file for model stages.")
    panels = ([("repaired-v3", CASES), ("transfer", TRANSFER), ("repeat-2", REPEATS), ("repeat-3", REPEATS)]
              if args.arm == "final-panels" else [(args.arm, args.cases)])
    if args.arm in ("v4-panels", "v5-panels", "v6-panels", "v7-panels"):
        tag = args.arm.split("-")[0]
        panels = [(tag,CASES), (tag+"-transfer",TRANSFER), (tag+"-r2",REPEATS),
                  (tag+"-r3",REPEATS), (tag+"-negative",(SYNTHETIC,))]
    jobs = [(arm, cid) for arm, cases in panels for cid in cases]
    with patch("tracegraph.providers.datetime", StudyClock), ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run, jobs))


if __name__ == "__main__":
    main()
