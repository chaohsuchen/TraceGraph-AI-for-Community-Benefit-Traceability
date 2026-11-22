"""Judge which Golden facts the verified search passages establish, before extraction."""

import argparse
from pathlib import Path

from .nli_eval import _digest, _load, _now
from .nli_judge import _CAPTURE_SCHEMA, _NLI_RULES, _submit
from .nli_score import score_retrieval_facts
from .openai_provider import DEFAULT_OPENAI_MODEL, OpenAIClient
from .providers import _object
from .research_experiment import _write_checkpoint
from .runtime import read_credential


RUBRIC = _NLI_RULES + """

For each reference fact, rate whether the supplied verified excerpts establish the
entire statement. The reference facts are hypotheses. They supply no evidence.
Return each gold_id once. Cite only the passage IDs in this packet. Combine
passages only when they concern the same subject and obligation. If an attribution
is part of the hypothesis, check who actually made or recorded that claim. Do not
infer an unstated payer, location, beneficiary or qualification. An absent or
incompletely supported fact is NEUTRAL. Use empty citation_ids when no passage
bears on the claim. Give a brief reason identifying the decisive text or gap.
"""
SCHEMA = _object({"facts": _CAPTURE_SCHEMA["properties"]["facts"]})


def capture_packet(reference, events):
    """Use the same data for either research outcome and assign stable passage IDs."""
    seen_capture = False
    unique = {}
    for event in events:
        if event.get("type") != "research_capture":
            continue
        seen_capture = True
        for citation in event["citations"].values():
            passage = {field: citation[field] for field in ("url", "title", "excerpt")}
            unique.setdefault((passage["url"], passage["excerpt"]), passage)
    passages = {f"p{n}": unique[key] for n, key in enumerate(sorted(unique), start=1)}
    facts = [{"id": f["id"], "text": f["text"]} for f in reference["facts"]]
    return seen_capture, {"reference_facts": facts, "passages": passages}


def evaluate(run_dir, model, key_file=None, *, replay=False):
    reference, research = (_load(run_dir / filename) for filename in ("reference.json", "research.json"))
    if _digest(reference) != research["reference_sha256"] or not research.get("finished_at"):
        raise ValueError("Expected a finished run with its original frozen reference.")
    events_path = run_dir / "research-events.json"
    seen_capture, packet = capture_packet(reference, _load(events_path) if events_path.exists() else [])
    passages = packet["passages"]
    status = "AVAILABLE" if passages else "EMPTY" if seen_capture else "UNAVAILABLE"
    fingerprint = {"judge_model": model, "case_id": reference["case_id"], "capture_status": status,
                   "research_outcome": research["outcome"], "input_sha256": _digest(packet),
                   "rubric_sha256": _digest([RUBRIC, SCHEMA])}
    path = run_dir / "retrieval-judgment.json"
    saved = {**fingerprint, "started_at": _now(), "calls": [], "passages": passages}
    if path.exists():
        saved = _load(path)
        if any(saved.get(k) != v for k, v in fingerprint.items()) or saved["passages"] != passages:
            raise ValueError("Saved retrieval judgment belongs to different inputs, model, or rubric.")
        if saved.get("finished_at"):
            expected = None if not seen_capture else score_retrieval_facts(reference, passages, saved["facts"])
            if saved["score"] != expected or (not seen_capture and saved["facts"] is not None):
                raise ValueError("Stored score does not agree with the frozen retrieval judgment.")
            return saved
    if replay:
        raise ValueError("No completed retrieval judgment to replay.")
    facts = None
    if passages:
        def emit(event):
            saved["calls"].append(event)
            _write_checkpoint(path, saved)

        client = OpenAIClient(api_key=read_credential(key_file, provider="openai"),
                              model=model, allow_web_search=False, emit=emit)
        error = None
        for attempt in range(2):
            answer = _submit(client, RUBRIC, packet.copy(), SCHEMA, 4096, error)
            facts = answer["facts"]
            try:
                score_retrieval_facts(reference, passages, facts)
                break
            except ValueError as exc:
                error = str(exc)
                saved.setdefault("invalid_responses", []).append({"response": answer, "error": error})
                _write_checkpoint(path, saved)
                if attempt:
                    raise
    elif seen_capture:
        facts = [{"gold_id": f["id"], "label": "NEUTRAL", "citation_ids": [],
                  "reason": "Completed capture supplied no verified passages."} for f in packet["reference_facts"]]
    saved.update(facts=facts, score=score_retrieval_facts(reference, passages, facts) if seen_capture else None,
                 finished_at=_now())
    _write_checkpoint(path, saved)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_OPENAI_MODEL)
    parser.add_argument("--openai-key-file", type=Path)
    parser.add_argument("--replay", action="store_true", help="Verify a completed score without any API call.")
    args = parser.parse_args()
    result = evaluate(args.run_dir, args.model, args.openai_key_file, replay=args.replay)
    print(result["case_id"], result["capture_status"], result["score"])


if __name__ == "__main__":
    main()
