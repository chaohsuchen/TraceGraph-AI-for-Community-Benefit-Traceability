"""Normalize user-visible outputs for evaluation without promoting diagnostic data."""

from copy import deepcopy


PACKET_VERSION = "tracegraph-eval-packet-v1"


def normalize_graph(graph):
    if not isinstance(graph, dict) or not isinstance(graph.get("findings"), list):
        raise ValueError("A normalized graph requires a findings array")
    result = {key: deepcopy(graph.get(key)) for key in ("commitment", "organization", "assessment")}
    result["findings"] = []
    ids, support_ids = set(), set()
    for index, finding in enumerate(graph["findings"], 1):
        if not isinstance(finding, dict) or not isinstance(finding.get("claim"), str) or not finding["claim"].strip():
            raise ValueError("Each user-visible finding requires a nonempty claim")
        fid = finding.get("id", f"f{index}")
        if not isinstance(fid, str) or not fid.strip() or fid in ids:
            raise ValueError("Finding IDs must be nonempty and unique")
        ids.add(fid)
        supports = finding.get("supports", [])
        if not isinstance(supports, list):
            raise ValueError("Finding supports must be an array, including when empty")
        normalized = []
        for number, support in enumerate(supports, 1):
            if not isinstance(support, dict):
                raise ValueError("Each support must be an object")
            sid = support.get("id", f"{fid}:s{number}")
            if not isinstance(sid, str) or not sid.strip() or sid in support_ids:
                raise ValueError("Support IDs must be globally unique per attachment")
            support_ids.add(sid)
            excerpt = support.get("excerpt")
            if excerpt is not None and not isinstance(excerpt, str):
                raise ValueError("A support excerpt must be a string or null")
            source = support.get("source") or {}
            if not isinstance(source, dict):
                raise ValueError("A support source must be an object")
            normalized.append({"id": sid, "excerpt": excerpt,
                               "locator": support.get("locator"), "source": deepcopy(source)})
        result["findings"].append({"id": fid, "claim": finding["claim"], "supports": normalized})
    return result


def prediction_from_research(research):
    """Only graph findings or explicit PARTIAL_RESEARCH facts count as returned claims."""
    outcome = research.get("outcome")
    if outcome in ("RESOLVED", "RETURNED") and research.get("graph") is not None:
        return normalize_graph(research["graph"]), "GRAPH"
    public = research.get("job_result") or {}
    if not isinstance(public, dict):
        raise ValueError("job_result must be an object")
    if outcome == "PARTIAL_RESEARCH" and public.get("outcome") == "PARTIAL_RESEARCH":
        facts = public.get("facts", [])
        if not isinstance(facts, list):
            raise ValueError("PARTIAL_RESEARCH job_result.facts must be an array")
        findings = []
        for index, fact in enumerate(facts, 1):
            if not isinstance(fact, dict):
                raise ValueError("Each partial fact must be a source excerpt object")
            quote = fact.get("excerpt")
            if not isinstance(quote, str) or not quote.strip():
                raise ValueError("A visible partial fact must contain a nonempty original excerpt")
            findings.append({"id": f"partial:f{index}", "claim": quote, "supports": [{
                "id": f"partial:s{index}", "excerpt": quote,
                "source": {"url": fact.get("source_url"), "title": fact.get("source_title")},
            }]})
        # The original query, gaps, suggestions, unmatched records and capture are
        # not a newly established commitment or independent source of facts.
        return normalize_graph({"findings": findings, "assessment": None}), "PARTIAL_FACTS"
    return None, "NONE"


def has_verdict(prediction):
    assessment = prediction.get("assessment") or {}
    return isinstance(assessment, dict) and any(
        isinstance(assessment.get(key), str) and assessment[key].strip() for key in ("label", "verdict"))


def verdict_applicable(reference, prediction):
    return not reference_not_applicable(reference) and has_verdict(prediction)


def reference_not_applicable(reference):
    original = reference.get("reference_verdict") or {}
    return (reference.get("delivery_applicable") is False
            or isinstance(original, dict) and original.get("delivery_applicable") is False
            or any(reference.get(key) == "NOT_APPLICABLE" for key in ("applicability", "verdict_applicability")))
