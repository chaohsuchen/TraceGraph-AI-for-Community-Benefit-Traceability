"""Run a cold-start Golden case, judge frozen evidence, or replay its score."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic

from tracegraph.openai_provider import DEFAULT_OPENAI_MODEL
from tracegraph.nli_packet import PACKET_VERSION, prediction_from_research, verdict_applicable, reference_not_applicable
from tracegraph.research_experiment import _replay_strategy, _write_checkpoint
from tracegraph.runtime import LOCAL_DSN, ROOT, create_application, evaluation_catalog, read_credential


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _trace_links(trace, graph):
    """Map transient assessment references to the supports saved by PostgreSQL."""
    for citation in trace.get("citations", {}).values():
        source = citation["source"]
        citation["support_ids"] = [
            support["id"] for finding in graph["findings"] if finding["id"] == citation["finding_id"]
            for support in finding["supports"]
            if support.get("excerpt") == citation["excerpt"]
            and support.get("locator") == citation.get("locator")
            and (support["source"].get("url") or support["source"].get("citation"))
            == (source.get("url") or source.get("citation"))
        ]


def research_case(query, dsn, key_file, model, emit, *, web_search_model=None, frozen_capture=None):
    assessments = []
    replay_options = {}
    if frozen_capture is not None:
        replay_options = {"search_strategy": _replay_strategy(None, frozen_capture),
                          "allow_web_search": False, "enable_conflict_review": False}
    with evaluation_catalog(dsn) as scoped:
        app = create_application(scoped, "live", provider="openai", key_file=key_file, model=model,
                                 emit=emit, on_assessment=assessments.append, web_search_model=web_search_model,
                                 **replay_options)
        status, _, body = app.respond("POST", "/api/v1/lookup", {"Idempotency-Key": "nli-eval"},
                                      json.dumps({"query": query}).encode())
        if status != 202 or not app.process_one_job():
            raise RuntimeError("Cold-start lookup did not queue and process a research job.")
        job = app.store.get_job(body["job_id"])
        if job["status"] != "COMPLETED":
            return {"outcome": "FAILED", "error": job.get("error"), "graph": None}
        result = job["result"]
        replay_result = {"job_result": result}
        if result["outcome"] != "RESOLVED":
            return {"outcome": result["outcome"], "message": result.get("message"), "graph": None, **replay_result}
        status, _, graph = app.respond("GET", f'/api/v1/commitments/{result["commitment_id"]}', {})
        if status != 200 or graph != app.store.graph(result["commitment_id"]):
            raise RuntimeError("The user graph endpoint did not return the persisted assessment.")
        if assessments:
            trace = assessments[-1]
            _trace_links(trace, graph)
            graph["assessment_trace"] = trace
        return {"outcome": "RESOLVED", "graph": graph, **replay_result}


def _research(args):
    reference = _load(args.reference)
    run_dir = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_checkpoint(run_dir / "reference.json", reference)
    query = args.query or reference["query"]
    meta = {"case_id": reference["case_id"], "query": query, "model": args.model,
            "web_search_model": args.web_search_model or args.model,
            "started_at": _now(), "reference_sha256": _digest(reference),
            "research_code_sha256": {p.name: sha256(p.read_bytes()).hexdigest()
                                     for p in sorted((ROOT / "tracegraph").glob("*.py"))
                                     if not p.name.startswith("nli_")}}
    events = []

    def emit(event):
        events.append(event)
        _write_checkpoint(run_dir / "research-events.json", events)
        print(event.get("type") or event.get("phase"), flush=True)

    started = monotonic()
    _write_checkpoint(run_dir / "research.json", meta)
    result = {"outcome": "FAILED", "graph": None}
    try:
        result = research_case(query, args.database_url, args.openai_key_file, args.model, emit,
                               web_search_model=args.web_search_model)
    except Exception as exc:
        result["error"] = type(exc).__name__
        raise
    finally:
        _write_checkpoint(run_dir / "research.json", {**meta, **result, "finished_at": _now(),
                                                      "seconds": round(monotonic() - started, 2)})
    print(result["outcome"])


def _inputs(run_dir=None, *, packet=None, reference_path=None):
    reference = _load(reference_path or run_dir / "reference.json")
    research = _load(packet or run_dir / "research.json")
    if packet and (research.get("schema_version") != PACKET_VERSION or research.get("case_id") != reference["case_id"]):
        raise ValueError("Baseline packet version or case_id does not match the reference")
    if packet:
        if any(not isinstance(research.get(key), str) or not research[key].strip() for key in ("query", "system")):
            raise ValueError("Baseline packet requires query and system strings")
        if research.get("outcome") not in ("RETURNED", "RESOLVED", "PARTIAL_RESEARCH", "NO_OUTPUT", "FAILED", "AMBIGUOUS", "NO_PUBLIC_PROMISE"):
            raise ValueError("Unknown baseline packet outcome")
        if research["outcome"] in ("RETURNED", "RESOLVED") and research.get("graph") is None:
            raise ValueError("A positive packet requires a graph; use NO_OUTPUT for no returned answer")
    if (not packet or research.get("reference_sha256")) and _digest(reference) != research.get("reference_sha256"):
        raise ValueError("Frozen reference has changed.")
    if not research.get("finished_at"):
        raise ValueError("Research is still incomplete.")
    passages = {}
    if not packet and research["outcome"] in ("NO_PUBLIC_PROMISE", "PARTIAL_RESEARCH"):
        event_path = run_dir / "research-events.json"
        events = _load(event_path) if event_path.exists() else []
        seen = set()
        for event in events:
            if event.get("type") not in ("research_capture", "research_recovery_capture"):
                continue
            for passage in event["citations"].values():
                key = (passage["url"], passage["excerpt"])
                if key not in seen:
                    passages[f"p{len(passages) + 1}"] = {k: passage[k] for k in ("url", "title", "excerpt")}
                    seen.add(key)
    return reference, research, passages


def _score(run_dir, results_dir, *, packet=None, reference_path=None):
    from tracegraph.nli_score import format_capture_report, format_case_report, score_capture, score_case

    reference, research, passages = _inputs(run_dir, packet=packet, reference_path=reference_path)
    prediction, output_kind = prediction_from_research(research)
    if prediction is not None or passages:
        record = _load(results_dir / "judgment.json")
        if record["input_sha256"] != _digest([reference, research, passages]):
            raise ValueError("Judgment belongs to a different frozen case.")
    if prediction is not None:
        judgment = record["judgment"]
        score = score_case(reference, prediction, judgment)
        report = format_case_report(reference, prediction, judgment, score)
    else:
        score = {"case_id": reference["case_id"], "outcome": research["outcome"], "identity": "UNRESOLVED",
                 "reference_coverage": {"numerator": 0, "denominator": len(reference["facts"]), "rate": 0 if reference["facts"] else None},
                 "finding_support": {"numerator": 0, "denominator": 0, "rate": None},
                 "traceability": {"numerator": 0, "denominator": 0, "rate": None}, "verdict": None,
                 "claim_count": 0, "evidence_eligible_claims": 0, "unquoted_claims": 0,
                 "citation_audit": "NOT_APPLICABLE", "credited_reference_ids": [],
                 "coverage_labels": {"ENTAILMENT": 0, "CONTRADICTION": 0, "NEUTRAL": 0, "MISSING": len(reference["facts"])},
                 "finding_labels": {"ENTAILMENT": 0, "CONTRADICTION": 0, "NEUTRAL": 0},
                 "verdict_evaluation": {"applicable": False, "reason": "NO_USER_VISIBLE_OUTPUT", "label": None}}
        report = (f'# {reference["case_id"]}\n\nResearch outcome: {research["outcome"]}. '
                  f'End-to-end coverage: 0/{len(reference["facts"])}. '
                  'No graph was available for finding or verdict evaluation.\n')
    if passages and "capture_judgment" in record:
        diagnostic = score_capture(reference, passages, record["capture_judgment"])
        score["retrieval_diagnostic"] = diagnostic
        report += "\n" + format_capture_report(reference, passages, record["capture_judgment"], diagnostic,
                                               visible_coverage=score["reference_coverage"])
    public = research.get("job_result") or {}
    score.update(outcome=research["outcome"], output_kind=output_kind,
                 system=research.get("system") or "TraceGraph",
                 case_applicability=reference.get("applicability", "APPLICABLE"),
                 delivery_applicable=not reference_not_applicable(reference),
                 user_visible_findings=len(prediction["findings"]) if prediction is not None else 0,
                 source_counts={"capture_passages": len(passages),
                                "unmatched_records": len(public.get("unmatched_records", [])),
                                "suggestions": len(public.get("suggestions", []))},
                 score_version="tracegraph-score-v4")
    _write_checkpoint(results_dir / "score.json", score)
    (results_dir / "report.md").write_text(report, encoding="utf-8")


def _judge(args, results_dir):
    from tracegraph.nli_judge import JUDGE_VERSION, rubric_digest, judge_capture, judge_evidence, judge_verdict
    from tracegraph.nli_score import score_capture, score_case, validate_evidence
    from tracegraph.openai_provider import OpenAIClient

    run_dir = args.run_dir
    packet, reference_path = getattr(args, "packet", None), getattr(args, "reference", None)
    reference, research, passages = _inputs(run_dir, packet=packet, reference_path=reference_path)
    prediction, _ = prediction_from_research(research)
    score_options = {"packet": packet, "reference_path": reference_path}
    if prediction is None and not passages:
        _score(run_dir, results_dir, **score_options)
        return
    graph = prediction
    identity = {"input_sha256": _digest([reference, research, passages]), "judge_model": args.model,
                "rubric_sha256": rubric_digest(), "judge_version": JUDGE_VERSION}
    path = results_dir / "judgment.json"
    if path.exists():
        saved = _load(path)
        if any(saved.get(k) != v for k, v in identity.items()):
            raise ValueError("Frozen judgment uses different inputs, model or rubric.")
        if (prediction is None or "judgment" in saved) and (not passages or "capture_judgment" in saved):
            _score(run_dir, results_dir, **score_options)
            return
    else:
        saved = {**identity, "started_at": _now(), "calls": []}

    def emit(event):
        saved["calls"].append(event)
        _write_checkpoint(path, saved)

    client = OpenAIClient(api_key=read_credential(args.openai_key_file, provider="openai"),
                          model=args.model, allow_web_search=False, emit=emit)

    def validated(stage, request, check):
        correction = None
        for attempt in range(2):
            reply = request(correction)
            try:
                check(reply)
                return reply
            except ValueError as exc:
                correction = str(exc)
                saved.setdefault("invalid_responses", []).append(
                    {"stage": stage, "response": reply, "error": correction})
                _write_checkpoint(path, saved)
                if attempt:
                    raise

    if prediction is not None:
        if "evidence" not in saved:
            saved["evidence"] = validated(
                "evidence", lambda error: judge_evidence(client, reference, graph, correction=error),
                lambda reply: validate_evidence(reference, graph, reply, require_quote_checks=True))
            _write_checkpoint(path, saved)
        evidence = {**saved["evidence"], "protocol": "owned-quotes-v1"}
        saved["verdict_protocol"] = "two-stage-owned-citations-v4"
        verdict = None
        if verdict_applicable(reference, graph):
            verdict = validated(
                "verdict", lambda error: judge_verdict(client, reference, graph, evidence, correction=error),
                lambda reply: score_case(reference, graph, {**evidence, "verdict": reply}))
        saved["judgment"] = {**evidence, "verdict": verdict}
        _write_checkpoint(path, saved)
    if passages:
        outcome = {k: research.get(k) for k in ("outcome", "message")}
        target = {**reference, "query": research["query"]}
        saved["capture_judgment"] = validated(
            "capture", lambda error: judge_capture(client, target, outcome, passages, correction=error),
            lambda reply: score_capture(reference, passages, reply))
    saved["finished_at"] = _now()
    _write_checkpoint(path, saved)
    _score(run_dir, results_dir, **score_options)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("research", "judge", "score"):
        action = actions.add_parser(name)
        if name == "research":
            action.add_argument("--run-dir", type=Path, required=True)
        else:
            source = action.add_mutually_exclusive_group(required=True)
            source.add_argument("--run-dir", type=Path)
            source.add_argument("--packet", type=Path, help="Read a sealed normalized baseline packet without changing it.")
            action.add_argument("--reference", type=Path, help="Required for --packet; evaluator-only reference.")
        if name != "research":
            action.add_argument("--results-dir", type=Path,
                                help="Separate directory for a new judge model or rubric on this frozen run.")
        if name != "score":
            action.add_argument("--openai-key-file", type=Path)
            action.add_argument("--model", default=DEFAULT_OPENAI_MODEL)
        if name == "research":
            action.add_argument("--reference", type=Path, default=ROOT / "evaluation/nli/h010.json")
            action.add_argument("--query", help="Alternate user wording for this same case, saved in the run.")
            action.add_argument("--web-search-model", help="Override only the model that calls web search.")
            action.add_argument("--database-url", default=os.environ.get("TRACEGRAPH_DATABASE_URL", LOCAL_DSN))
    args = parser.parse_args()
    if args.action == "research":
        _research(args)
    else:
        if args.packet and (args.reference is None or args.results_dir is None):
            parser.error("--packet requires --reference and --results-dir")
        if args.run_dir and args.reference:
            parser.error("--reference is for --packet; native runs use their frozen reference.json")
        results_dir = args.results_dir or args.run_dir
        inputs = [args.packet, args.reference] if args.packet else [args.run_dir / "reference.json", args.run_dir / "research.json", args.run_dir / "research-events.json"]
        if any((results_dir / name).resolve() == path.resolve() for name in ("judgment.json", "score.json", "report.md")
               for path in inputs if path is not None):
            parser.error("Output paths must not overwrite frozen input files")
        if args.action == "judge":
            _judge(args, results_dir)
        else:
            _score(args.run_dir, results_dir, packet=args.packet, reference_path=args.reference)


if __name__ == "__main__":
    main()
