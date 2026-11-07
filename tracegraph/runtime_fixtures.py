"""Deterministic external responses for PostgreSQL and browser tests."""

from datetime import date, datetime, timezone


def fixture_judge(identity, candidates):
    text = (identity.get("query") or identity["discovered"]["description"]).lower()
    candidate_ids = {row["commitment_id"] for row in candidates}
    matched = None
    if any(word in text for word in ("public safety", "substation", "nypd")):
        matched = "TG-GOLD-H008"
    elif any(word in text for word in ("renewable", "solar", "electricity")):
        matched = "TG-GOLD-H010"
    elif "bronx" in text and any(word in text for word in ("hiring", "jobs", "workforce")):
        if "construction" in text:
            matched = "TG-GOLD-H009"
        elif "permanent" in text:
            matched = "TG-GOLD-H001"
        else:
            return {"outcome": "AMBIGUOUS", "question": "Do you mean permanent operations or construction and facility jobs?"}
    elif "overnight electric shuttle" in text:
        matched = next(
            (row["commitment_id"] for row in candidates if "overnight electric shuttle" in row["description"].lower()),
            None,
        )
    if matched in candidate_ids:
        return {"outcome": "MATCH", "commitment_id": matched}
    return {"outcome": "NO_MATCH"}


def fixture_research(job, context):
    if context and job["commitment_id"] == "TG-GOLD-H010":
        raise RuntimeError("Simulated external research failure")
    if not context and "overnight electric shuttle" not in job["query"].lower():
        return {"draft": None}

    new_description = "Example Transit Authority promises an overnight electric shuttle pilot in 2028."
    existing = context["commitment"] if context else None
    excerpt = (existing["original_text"] or existing["description"]) if existing else new_description
    original = (existing or {}).get("original_promise") or {}
    source = original.get("source") or {
        "title": "Fixture public statement",
        "url": "https://example.com/tracegraph-commitment",
        "citation": "Simulated statement, section 2",
        "publisher": "Example Transit Authority",
        "source_type": "fixture",
        "published_on": None,
    }
    locator = original.get("locator") or "Section 2"
    result = {
        "verification_date": date.today().isoformat(),
        "verdict": "Delivery remains unverified.",
        "reasoning": "The fixture records the original promise and supplies no evidence of delivery.",
        "evidence": [{
            "source": {**source, "source_id": source.get("id")},
            "status": "Commitment documented",
            "explanation": "The cited passage sets out the commitment.",
            "excerpt": excerpt,
            "summary": None,
            "locator": locator,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }],
    }
    if context:
        return {"result": result}
    return {
        "result": result,
        "draft": {
            "title": "Overnight electric shuttle pilot",
            "description": new_description,
            "original_text": excerpt,
            "organization_name": "Example Transit Authority",
            "responsible_entity_raw": "Example Transit Authority",
            "beneficiary": {"category": "transit riders", "name": None},
            "term_periodicity": "2028 pilot",
            "original_promise": {"source": source, "locator": locator, "excerpt": excerpt},
        },
    }
