"""Bounded, read-only reproduction of a discovery request. Records stages, not response bodies."""
import argparse
import json
from pathlib import Path
import sys
from time import monotonic
import traceback
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--case-record", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Choose a new diagnostic output path.")
    sys.path.insert(0, str(args.runtime.resolve()))
    from tracegraph.experimental_prompt import search
    from tracegraph.openai_provider import OpenAIClient, OpenAIWebResearcher
    from tracegraph.runtime import read_credential

    query = json.loads(args.case_record.read_text(encoding="utf-8"))["query"]
    events = []

    def emit(event):
        kind = event.get("type")
        if kind == "provider_call":
            events.append({k: event.get(k) for k in (
                "type", "model", "status", "seconds", "usage", "stop_reason", "search_enabled")})
        elif kind in ("research_capture", "research_recovery_capture", "research_extracted"):
            events.append({"type": kind, "citations": len(event.get("citations", {})),
                           "draft": bool(event.get("draft"))})
        elif kind in ("search_budget", "research_interrupted", "search_retry", "search_error"):
            events.append({k: v for k, v in event.items() if k != "reason"})

    client = OpenAIClient(read_credential(args.key_file, provider="openai"), "gpt-5.4",
                          emit=emit, deadline=monotonic() + 240)
    researcher = OpenAIWebResearcher(client, search_strategy=search, emit=emit)
    request = client._request

    def observe(*a, **kw):
        result = request(*a, **kw)
        if kw.get("tools"):
            items = result["output"]
            events.append({"type": "web_shape", "requested": kw.get("max_tool_calls"),
                           "tools": [[item.get("status"), (item.get("action") or {}).get("type")]
                                     for item in items if item.get("type") == "web_search_call"],
                           "message_count": sum(item.get("type") == "message" for item in items)})
        return result

    try:
        with patch.object(client, "_request", observe):
            result = researcher({"kind": "DISCOVER", "query": query}, None)
        events.append({"type": "result", "status": "returned", "draft": bool(result.get("draft")),
                       "related": len(result.get("related_evidence", [])),
                       "unmatched": len(result.get("unmatched_evidence", [])),
                       "leads": len(result.get("related_sources", []))})
    except Exception as exc:
        events.append({"type": "failure", "class": type(exc).__name__,
                       "frames": [[Path(f.filename).name, f.lineno, f.name]
                                  for f in traceback.extract_tb(exc.__traceback__)[-8:]]})
    with args.out.open("x", encoding="utf-8") as out:
        json.dump(events, out, indent=2)
    print(json.dumps([e for e in events if e["type"] in ("web_shape", "result", "failure")]))


if __name__ == "__main__":
    main()
