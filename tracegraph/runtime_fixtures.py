"""Synthetic evidence lifecycle used by integration and browser tests."""

from datetime import datetime, timezone


FIXTURE_QUERY = "What did Example Transit Authority promise about an overnight electric shuttle pilot?"
ORIGINAL = "The agency committed to launch an overnight electric shuttle pilot in 2028."
BUDGET = "The agency approved funding for the 2028 overnight shuttle pilot."
ANNOUNCEMENT = "The announcement records three shuttles deployed in January 2028."
AUDIT = "The audit records two shuttles deployed in January 2028."


def fixture_judge(identity, candidates):
    text = (identity.get("query") or identity["discovered"]["description"]).lower()
    if "overnight electric shuttle" in text:
        for row in candidates:
            if "overnight electric shuttle" in row["description"].lower():
                return {"outcome": "MATCH", "commitment_id": row["commitment_id"]}
    return {"outcome": "NO_MATCH"}


def _citation(document, title, claim, status):
    return {
        "source": {
            "title": title, "url": "https://example.org/tracegraph-fixture/" + document,
            "publisher": "Example Transit Authority", "citation": None,
            "source_type": "synthetic fixture", "published_on": None,
        },
        "status": status, "explanation": claim, "excerpt": claim, "summary": claim,
        "locator": "Section 1", "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


def fixture_research(job, context):
    if not context and "overnight electric shuttle" not in job["query"].lower():
        return {"draft": None}
    original = _citation("promise", "Public pilot commitment", ORIGINAL, "Commitment documented")
    evidence = [original, _citation("budget", "Pilot budget approval", BUDGET, "Budget approved")]
    if context:
        evidence.extend((
            _citation("announcement", "Agency announcement", ANNOUNCEMENT, "Deployment reported"),
            _citation("audit", "Independent audit", AUDIT, "Deployment verified"),
        ))
    result = {
        "verification_date": datetime.now(timezone.utc).date().isoformat(),
        "verdict": "The original commitment is documented.",
        "reasoning": "Synthetic test response.", "evidence": evidence,
    }
    if context:
        return {"result": result}
    return {"result": result, "draft": {
        "title": "Overnight electric shuttle pilot", "description": ORIGINAL,
        "original_text": ORIGINAL, "organization_name": "Example Transit Authority",
        "responsible_entity_raw": "Example Transit Authority",
        "beneficiary": {"category": "transit riders", "name": None},
        "term_periodicity": "2028 pilot",
        "original_promise": {"source": original["source"], "locator": original["locator"], "excerpt": ORIGINAL},
    }}


def fixture_review(context, pairs):
    return [_citation("vehicle-register", "Vehicle deployment register", AUDIT, "Audit independently corroborated")]


def fixture_reconcile(context, snapshot, observations, reviewed_pairs):
    ids = {finding["claim"]: finding["id"] for finding in snapshot["findings"]}
    assignments = []
    review_refs = []
    for index, observation in enumerate(observations, 1):
        claim = observation["support"]["summary"]
        finding_id = ids.setdefault(claim, f"new:{index}")
        assignments.append({"observation_id": observation["id"], "finding_id": finding_id, "claim": claim})
        if observation.get("from_review"):
            review_refs.append(observation["id"])

    relations = []
    for older, newer in [(ORIGINAL, BUDGET), (BUDGET, ANNOUNCEMENT), (BUDGET, AUDIT)]:
        if older in ids and newer in ids:
            relations.append({
                "kind": "PROGRESS", "from_id": ids[older], "to_id": ids[newer],
                "reasoning": "A later milestone for the same pilot.", "status": None,
                "preferred_id": None, "support_ids": [],
            })

    has_conflict = ANNOUNCEMENT in ids and AUDIT in ids
    prior = next((edge for edge in snapshot["relations"] if edge["kind"] == "CONFLICT" and
                  {edge["from_id"], edge["to_id"]} == {ids.get(ANNOUNCEMENT), ids.get(AUDIT)}), None)
    resolved = bool(review_refs) or bool(prior and prior["status"] == "RESOLVED")
    if has_conflict and (not prior or review_refs):
        relations.append({
            "kind": "CONFLICT", "from_id": ids[ANNOUNCEMENT], "to_id": ids[AUDIT],
            "reasoning": "The vehicle register corroborates the audit." if resolved else "Different counts for the same month.",
            "status": "RESOLVED" if resolved else "UNRESOLVED", "preferred_id": ids[AUDIT] if resolved else None,
            "support_ids": review_refs,
        })
    if has_conflict:
        assessment = {"label": "PARTIALLY_CORROBORATED" if resolved else "CONFLICTING",
                      "verdict": "The audit supports deployment of two shuttles." if resolved else "Deployment counts conflict.",
                      "reasoning": "The promise and budget are documented. The announcement and audit report different counts."}
    else:
        assessment = {"label": "UNVERIFIED", "verdict": "Commitment documented; delivery unverified.",
                      "reasoning": "The promise and approved budget are documented."}
    return {"assignments": assignments, "relations": relations, "assessment": assessment}
