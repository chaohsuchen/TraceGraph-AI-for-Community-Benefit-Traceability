"""Replay the exact saved search output with the original or repaired TraceGraph adapter."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--online-sources", action="store_true", help="Fetch current public pages for quote checks")
    args = parser.parse_args()
    sys.path.insert(0, str(args.runtime.resolve()))
    from tracegraph.openai_provider import OpenAIClient, OpenAIWebResearcher
    from tracegraph.providers import ProviderError

    root = Path(__file__).resolve().parent
    record = root / "TG-GOLD-H001-paired-response.json"
    audit = json.loads((root / "H001-paired-result.json").read_text(encoding="utf-8"))
    response = json.loads(record.read_text(encoding="utf-8"))
    encoded = json.dumps(response, ensure_ascii=False, sort_keys=True).encode()
    assert sha256(encoded).hexdigest() == audit["response_content_sha256"]
    text = next(item for item in response["output"] if item["type"] == "message")["content"][0]["text"]
    proposals = json.loads(text)["passages"]
    expected = {(row["url"], row["sha256"]) for row in audit["verified_excerpts"]}
    pages = {}
    for row in proposals:
        if (row["url"], sha256(row["quote"].encode()).hexdigest()) in expected:
            pages.setdefault(row["url"], []).append(row["quote"])
    # Offline projections reproduce the publicly rechecked quote matches. No
    # new factual claims are inferred from these minimal page fixtures.
    loader = None if args.online_sources else lambda url, **kwargs: " ".join(pages.get(url, []))
    client = OpenAIClient("offline-replay")
    researcher = OpenAIWebResearcher(client, source_loader=loader)
    with patch.object(client, "_request", return_value=response):
        try:
            _, citations = researcher._search({"kind": "DISCOVER", "target": "saved query"}, max_uses=4)
        except ProviderError as error:
            print(json.dumps({"status": "provider_error", "error": str(error), "verified_count": 0}))
            return
    print(json.dumps({"status": "returned", "attributable_urls": len(researcher._located_sources),
                      "verified_count": len(citations)}))


if __name__ == "__main__":
    main()
