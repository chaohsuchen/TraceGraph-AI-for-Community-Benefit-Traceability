"""Re-assess the actual combined H017 findings using its source-only frozen input."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch

from replay import ROOT, assess, assessor_inputs, digest, grade, read, source_dir, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("assess", "judge", "audit"))
    p.add_argument("--study", required=True, type=Path)
    p.add_argument("--coverage", required=True, type=Path, help="Path to the coverage-loss experiment")
    p.add_argument("--runtime", required=True, type=Path)
    p.add_argument("--arm", default="integration-v4")
    p.add_argument("--key-file", type=Path)
    args = p.parse_args()
    args.runtime = args.runtime.resolve()
    sys.path.insert(0, str(args.runtime))
    from tracegraph.providers import _assessment_citations
    cid = "TG-GOLD-H017"
    prior = source_dir(args.study, cid)
    trial = args.coverage / "results/discover" / cid
    old, new = (read(f / "research.json") for f in (prior, trial))
    context, state, day = assessor_inputs(prior)
    args.as_of = day
    old_nodes, combined = old["graph"]["findings"], new["graph"]["findings"]
    assert combined[:len(old_nodes)] == old_nodes and new["graph"]["assessment"] == old["graph"]["assessment"]
    extra = combined[len(old_nodes):]
    assert extra and not {f["id"] for f in state["findings"]} & {f["id"] for f in extra}
    before = _assessment_citations(state)
    state["findings"].extend(extra)
    after = _assessment_citations(state)
    assert len(after) == len(before) + sum(len(f["supports"]) for f in extra)
    assert all(after[k] == v for k, v in before.items())
    receipt = {"case": cid, "previous_research": digest(old), "augmented_research": digest(new),
               "previous_citations": len(before), "augmented_citations": len(after), "new_findings": len(extra),
               "new_source_passages": [v for k, v in after.items() if k not in before],
               "producer_inputs_sha256": digest([context, state, day])}
    dest = ROOT / "runs" / args.arm / cid
    if args.phase == "assess":
        class StudyClock(datetime):
            @classmethod
            def now(cls, tz=None):
                moment = cls.fromisoformat(day+"T12:00:00+00:00")
                return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
        write(dest / "inputs.json", receipt)
        with patch("tracegraph.providers.datetime", StudyClock):
            print(assess(args, cid, trial, frozen_inputs=(context, state, day)))
    elif args.phase == "judge":
        assert read(dest / "inputs.json") == receipt
        print(grade(args, cid, trial))
    else:
        from tracegraph.nli_packet import prediction_from_research
        from tracegraph.nli_score import score_case
        from tracegraph.nli_judge import rubric_digest
        saved = read(trial / "judgment.json")
        result = read(dest / "assessment.json")
        j = read(dest / "judge.json")
        assert read(dest / "inputs.json") == receipt and result["inputs_sha256"] == receipt["producer_inputs_sha256"]
        assert result["trace"]["citations"] == after and j["research_sha256"] == digest(new)
        graph, _ = prediction_from_research(new)
        graph["assessment"] = result["displayed"]
        assert digest(result["displayed"]) == j["displayed_sha256"] and j["rubric_sha256"] == rubric_digest()
        evidence = {**saved["evidence"], "protocol": "owned-quotes-v1"}
        assert j["score"] == score_case(read(trial/"reference.json"), graph, {**evidence, "verdict": j["result"]})
        print(cid, j["original"]["label"], "->", j["result"]["label"], "with", len(after), "owned citations")


if __name__ == "__main__":
    main()
