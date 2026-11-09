"""Score captured experiment outputs locally and estimate provider charges."""

from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path
from statistics import median

from tracegraph.benchmark import _reference_cases, _returned_evidence, _score
from tracegraph.runtime import WORKBOOK


MODEL = "claude-sonnet-4-6"
PRICE_DATE = "2026-11-08"
TOKEN_USD_PER_MILLION = {
    "input": 3, "output": 15, "cache_write_5m": 3.75, "cache_write_1h": 6, "cache_read": 0.30,
}
SEARCH_USD = 0.01


def estimate_usage(events):
    counts = Counter()
    search_ids = set()
    for event in events:
        if event.get("type") != "provider_call":
            continue
        counts["provider_calls"] += 1
        search_ids.update(event.get("web_search_ids") or ())
        usage = event.get("usage")
        if not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= usage.keys():
            counts["calls_missing_usage"] += 1
            continue
        counts["input"] += usage["input_tokens"]
        counts["output"] += usage["output_tokens"]
        counts["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
        written = usage.get("cache_creation_input_tokens", 0) or 0
        breakdown = usage.get("cache_creation") or {}
        five = breakdown.get("ephemeral_5m_input_tokens") or 0
        hour = breakdown.get("ephemeral_1h_input_tokens") or 0
        counts["cache_write_5m"] += five
        counts["cache_write_1h"] += hour
        counts["cache_write_unknown"] += max(0, written - five - hour)
        counts["billed_searches"] += (usage.get("server_tool_use") or {}).get("web_search_requests") or 0
    counts["observed_search_ids"] = len(search_ids)
    known_cost = sum(counts[name] * price for name, price in TOKEN_USD_PER_MILLION.items()) / 1_000_000
    known_cost += counts["billed_searches"] * SEARCH_USD
    return {
        "counts": dict(counts), "estimated_usd": round(known_cost, 6),
        "complete": not (counts["calls_missing_usage"] or counts["cache_write_unknown"]),
    }


def _event(events, name):
    return next((event for event in reversed(events) if event.get("type") == name), None)


def _research_events(events):
    start = next((i for i, event in enumerate(events) if event.get("type") == "research_start"), None)
    if start is None:
        return []
    end = next((i for i, event in enumerate(events) if event.get("type") == "research_extracted"), None)
    return events[start:end]


def _match_summary(references, evidence):
    result = _score(references, evidence, [])
    return {key: result[key] for key in ("counts", "matches", "possible_matches", "missed_reference_rows")}


def _score_run(run, references):
    events = run.get("events", [])
    capture = _event(events, "research_capture")
    extracted = _event(events, "research_extracted")
    capture_matches = None
    if capture is not None:
        passages = capture["citations"].values()
        evidence = [
            {"source": {"title": item["title"], "url": item["url"]}, "excerpt": item["excerpt"]}
            for item in passages
        ]
        capture_matches = _match_summary(references, evidence)
    graph = run.get("graph")
    assessed = None
    if graph:
        assessed = _match_summary(references, _returned_evidence(graph))
    return {
        "case": run["case"], "variant": run["variant"], "status": run["status"],
        "outcome": run.get("outcome"), "failure_stage": run.get("failure_stage"), "error": run.get("error"),
        "reference_count": len(references), "timings": run.get("timings"),
        "search_capture_matches": capture_matches,
        "extracted_verdict": (extracted.get("result") or {}).get("verdict") if extracted else None,
        "final_assessment": graph.get("assessment") if graph else None,
        "graph_matches": assessed, "research_usage": estimate_usage(_research_events(events)),
        "job_usage": estimate_usage(events),
    }


def score_report(report, workbook):
    if report.get("model") != MODEL:
        raise ValueError(f"This pricing snapshot is for {MODEL}.")
    references = _reference_cases(workbook)
    scored = [_score_run(run, references[f"TG-GOLD-{run['case']}"][1]) for run in report["runs"]]
    groups = {}
    for item in scored:
        groups.setdefault(item["variant"], []).append(item)
    totals = {}
    for variant, items in groups.items():
        times = [item["timings"]["job_seconds"] for item in items if item.get("timings")]
        cost = sum(item["job_usage"]["estimated_usd"] for item in items)
        totals[variant] = {
            "attempts": len(items),
            "completed_jobs": sum(item["status"] == "COMPLETED" for item in items),
            "graphs": sum(item["graph_matches"] is not None for item in items),
            "median_job_seconds": round(median(times), 3) if times else None,
            "estimated_usd_total": round(cost, 6), "estimated_usd_per_attempt": round(cost / len(items), 6),
            "runs_with_incomplete_usage": sum(not item["job_usage"]["complete"] for item in items),
        }
    return {
        "mode": report["mode"], "model": MODEL,
        "pricing": {"verified_on": PRICE_DATE, "token_usd_per_million": TOKEN_USD_PER_MILLION,
                    "search_usd": SEARCH_USD},
        "totals": totals, "runs": scored,
    }


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() in {args.report.resolve(), args.workbook.resolve()}:
        parser.error("Choose an output path separate from the raw report and workbook.")
    raw = json.loads(args.report.read_text(encoding="utf-8"))
    output = score_report(raw, args.workbook)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
