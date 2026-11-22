"""Verify six frozen-evidence pairs, structurally rescore each saved verdict and total metered usage."""
import argparse
from collections import Counter
from copy import deepcopy
import sys
from pathlib import Path

from replay import CASES, TRANSFER, REPEATS, ROOT, digest, read, source_dir, write


def costs(events):
    total = Counter()
    for call in events:
        assert not call.get("web_search_ids") and not call.get("search_enabled")
        u = call["usage"]
        total["input"] += u["input_tokens"]
        total["output"] += u["output_tokens"]
        total["cached"] += (u.get("input_tokens_details") or {}).get("cached_tokens", 0)
        total["calls"] += 1
    total["estimated_usd"] = (2.5*(total["input"]-total["cached"]) + 0.25*total["cached"] + 15*total["output"])/1e6
    return dict(total)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--judge-runtime", required=True, type=Path)
    parser.add_argument("--arms", nargs="+", default=["control", "repaired", "repaired-v2", "repaired-v3"])
    panel = parser.add_mutually_exclusive_group()
    panel.add_argument("--transfer", action="store_true", help="Verify the four separate transfer cases only.")
    panel.add_argument("--repeats", action="store_true", help="Verify the three draws on three prespecified cases.")
    flag = parser.add_mutually_exclusive_group()
    flag.add_argument("--v4", action="store_true", help="Verify the separately preregistered fourth candidate.")
    flag.add_argument("--v5", action="store_true", help="Verify the fifth candidate.")
    flag.add_argument("--v6", action="store_true", help="Verify the sixth candidate.")
    flag.add_argument("--v7", action="store_true", help="Verify the seventh candidate.")
    args = parser.parse_args()
    version = next((v for v in ("v4", "v5", "v6", "v7") if getattr(args, v)), None)
    sys.path.insert(0, str(args.judge_runtime.resolve()))
    from tracegraph.nli_judge import JUDGE_VERSION, rubric_digest
    from tracegraph.nli_packet import prediction_from_research
    from tracegraph.nli_score import score_case
    if args.transfer:
        args.arms = [version + "-transfer" if version else "transfer"]
    elif args.repeats:
        args.arms = [version,version+"-r2",version+"-r3"] if version else ["repaired-v3", "repeat-2", "repeat-3"]
    elif version:
        args.arms = ["control","repaired","repaired-v2","repaired-v3","v4"]
        args.arms.extend(v for v in ("v5","v6","v7") if v <= version)
    cases = TRANSFER if args.transfer else REPEATS if args.repeats else CASES
    result = {"model": "gpt-5.4", "judge": JUDGE_VERSION, "rubric_sha256": rubric_digest(), "cases": {}, "arms": {}}
    calls = {arm: {"assess": [], "judge": []} for arm in args.arms}
    for case in cases:
        original = source_dir(args.study, case)
        research, gold, grading = [read(original/f) for f in ("research.json","reference.json","judgment.json")]
        old, _ = prediction_from_research(research)
        details = {"archived": {"label": old["assessment"]["label"], "grade": grading["judgment"]["verdict"]["label"]}}
        research_hash = digest(research)
        pinned = None
        runtime = None
        for arm in args.arms:
            folder = ROOT / "runs" / arm / case
            assess, grade = read(folder/"assessment.json"), read(folder/"judge.json")
            if pinned is not None:
                assert assess["inputs_sha256"] == pinned, f"{arm}/{case} different research inputs"
            pinned = assess["inputs_sha256"]
            if args.repeats:
                if runtime is not None:
                    assert assess["runtime"] == runtime, f"{arm}/{case} runtime drifted across repetitions"
                runtime = assess["runtime"]
            assert assess["status"] == "COMPLETED" and grade["rubric_sha256"] == rubric_digest()
            assert grade["research_sha256"] == research_hash and grade["displayed_sha256"] == digest(assess["displayed"])
            graph = deepcopy(old)
            graph["assessment"] = assess["displayed"]
            evidence = {**grading["evidence"], "protocol": "owned-quotes-v1"}
            assert grade["evidence_judgment_sha256"] == digest(evidence)
            assert score_case(gold, graph, {**evidence, "verdict": grade["result"]}) == grade["score"]
            details[arm] = {"label": assess["displayed"]["label"], "grade": grade["result"]["label"],
                            "reason": grade["result"]["reason"], "criteria": [r["delivery_status"] for r in assess["trace"]["criteria"]]}
            calls[arm]["assess"].extend(assess["calls"])
            calls[arm]["judge"].extend(grade["calls"])
        result["cases"][case] = details
    for arm, phases in calls.items():
        result["arms"][arm] = {phase: costs(events) for phase, events in phases.items()}
        result["arms"][arm]["grades"] = dict(Counter(case[arm]["grade"] for case in result["cases"].values()))
    result["total_estimated_usd"] = sum(info[k]["estimated_usd"] for info in result["arms"].values() for k in ("assess", "judge"))
    name = "transfer-summary.json" if args.transfer else "repeat-summary.json" if args.repeats else "summary.json"
    if version:
        name = version + "-" + name
    write(ROOT/name, result)
    print(f"{len(cases)} cases in {len(calls)} arms verified, {result['total_estimated_usd']:.6f} estimated USD.")


if __name__ == "__main__":
    main()
