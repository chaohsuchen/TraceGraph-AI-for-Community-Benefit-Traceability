"""Validate the judge's references and count its case-level decisions."""

from collections import Counter
from urllib.parse import urlsplit

from tracegraph.nli_packet import verdict_applicable, reference_not_applicable
from tracegraph.public_excerpt import normalize_excerpt


NLI_LABELS = ("ENTAILMENT", "CONTRADICTION", "NEUTRAL")


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _indexed(items, key, group, expected=None):
    if not isinstance(items, list):
        raise ValueError(f"{group} must be an array")
    result = {}
    for row in items:
        if not isinstance(row, dict):
            raise ValueError(f"{group}: expected an object")
        identifier = _text(row.get(key), f"{group}.{key}")
        if identifier in result:
            raise ValueError(f"{group}: duplicate {identifier}")
        result[identifier] = row
    if expected is not None and set(result) != set(expected):
        raise ValueError(f"{group}: missing {sorted(set(expected) - set(result))}, "
                         f"unexpected {sorted(set(result) - set(expected))}")
    return result


def _decision(row, allowed, context):
    if not isinstance(row, dict) or row.get("label") not in allowed:
        raise ValueError(f"{context}: expected a label in {allowed}")
    _text(row.get("reason"), f"{context}.reason")
    return row["label"]


def _references(row, key, available, context):
    identifiers = row.get(key)
    if not isinstance(identifiers, list) or any(not isinstance(i, str) for i in identifiers):
        raise ValueError(f"{context}.{key} must be an array of IDs")
    if len(set(identifiers)) != len(identifiers) or set(identifiers) - set(available):
        raise ValueError(f"{context}.{key} contains a duplicate or ineligible ID")
    return identifiers


def _citations(row, label, available, context):
    ids = _references(row, "citation_ids", available, context)
    if label in ("ENTAILMENT", "CONTRADICTION"):
        if not ids:
            raise ValueError(f"{context}: {label} requires a cited passage")
        for citation in ids:
            _text(available[citation].get("excerpt"), f"{context}.{citation}.excerpt")
    return ids


def _source_url(support):
    source = support.get("source") or {}
    url = source.get("url")
    if not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url)
        return url if parts.scheme in ("http", "https") and parts.hostname and parts.username is None else ""
    except ValueError:
        return ""


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def validate_evidence(reference, prediction, evidence_judgment, *, require_quote_checks=False):
    """Validate pass one independently so retries can reuse a complete evidence review."""
    if not isinstance(evidence_judgment, dict):
        raise ValueError("judge evidence must be an object")
    facts = _indexed(reference.get("facts"), "id", "reference facts")
    findings = _indexed(prediction.get("findings"), "id", "system findings")
    identity = _decision(evidence_judgment.get("identity"), ("MATCH", "MISMATCH", "UNRESOLVED"), "identity")
    finding_reviews = _indexed(evidence_judgment.get("findings"), "finding_id", "judge findings", findings)

    citations = {}
    by_finding = {}
    atom_counts = Counter()
    traceable_atoms = 0
    evidence_eligible_atoms = 0
    for fid, finding in findings.items():
        supports = _indexed(finding.get("supports", []), "id", f"{fid} supports")
        if set(citations).intersection(supports):
            raise ValueError(f"{fid}: support ID is shared by different findings")
        citations.update(supports)
        by_finding[fid] = supports
        atoms = finding_reviews[fid].get("claims")
        if not isinstance(atoms, list) or not atoms:
            raise ValueError(f"{fid}: each finding needs at least one judged claim")
        seen = set()
        for atom in atoms:
            label = _decision(atom, NLI_LABELS, fid)
            claim = _text(atom.get("claim"), f"{fid}.claim")
            if claim in seen:
                raise ValueError(f"{fid}: duplicate atomic claim")
            seen.add(claim)
            ids = _citations(atom, label, supports, fid)
            _quote_checks(atom, supports, fid, require_quote_checks)
            atom_counts[label] += 1
            evidence_eligible_atoms += any(isinstance(s.get("excerpt"), str) and s["excerpt"].strip()
                                           for s in supports.values())
            if label == "ENTAILMENT" and all(_source_url(supports[i]) for i in ids):
                traceable_atoms += 1

    coverage = _indexed(evidence_judgment.get("coverage"), "gold_id", "judge coverage", facts)
    coverage_counts = Counter()
    credited = []
    for gid, item in coverage.items():
        label = _decision(item, (*NLI_LABELS, "MISSING"), gid)
        fids = _references(item, "finding_ids", findings, gid)
        eligible = {cid: support for fid in fids for cid, support in by_finding[fid].items()}
        ids = _citations(item, label, eligible, gid)
        if label == "MISSING" and (fids or ids):
            raise ValueError(f"{gid}: MISSING may not link a finding or passage")
        if label != "MISSING" and not fids:
            raise ValueError(f"{gid}: a reviewed match must identify the system finding")
        _quote_checks(item, eligible, gid, require_quote_checks)
        _claim_links(item, finding_reviews, require_quote_checks)
        coverage_counts[label] += 1
        if label == "ENTAILMENT" and ids and all(_source_url(eligible[id_]) for id_ in ids):
            credited.append(gid)

    return {
        "case_id": reference["case_id"], "identity": identity,
        "reference_coverage": _ratio(len(credited) if identity == "MATCH" else 0, len(facts)),
        "finding_support": _ratio(atom_counts["ENTAILMENT"], atom_counts.total()),
        "traceability": _ratio(traceable_atoms, evidence_eligible_atoms),
        "claim_count": atom_counts.total(), "evidence_eligible_claims": evidence_eligible_atoms,
        "unquoted_claims": atom_counts.total() - evidence_eligible_atoms,
        "credited_reference_ids": credited if identity == "MATCH" else [],
        "coverage_labels": {label: coverage_counts[label] for label in (*NLI_LABELS, "MISSING")},
        "finding_labels": {label: atom_counts[label] for label in NLI_LABELS},
    }


def score_case(reference, prediction, judgment):
    """Only the judge decides semantic alignment, entailment and verdict quality."""
    strict = judgment.get("protocol") == "owned-quotes-v1"
    metrics = validate_evidence(reference, prediction, judgment, require_quote_checks=strict)
    metrics["citation_audit"] = "OWNED_QUOTES_V1" if strict else "LEGACY_UNAUDITED"
    if not verdict_applicable(reference, prediction):
        return {**metrics, "verdict": None,
                "verdict_evaluation": {"applicable": False, "reason": "REFERENCE_NOT_APPLICABLE" if reference_not_applicable(reference) else "NO_DISPLAYED_VERDICT", "label": None}}
    verdict = judgment.get("verdict")
    verdict_label = _decision(verdict, ("JUSTIFIED", "OVERCLAIM", "UNDERCLAIM", "INDETERMINATE"), "verdict")
    if metrics["identity"] != "MATCH" and verdict_label != "INDETERMINATE":
        raise ValueError("Unmatched or unresolved identity requires an INDETERMINATE verdict judgment")
    conditions = _indexed(reference.get("conditions", []), "id", "reference conditions")
    condition_reviews = _indexed(verdict.get("conditions"), "condition_id", "judge conditions", conditions)
    citations = {s["id"]: s for f in prediction["findings"] for s in f.get("supports", [])}
    for cid, item in condition_reviews.items():
        label = _decision(item, NLI_LABELS, cid)
        _citations(item, label, citations, cid)
        _quote_checks(item, citations, cid, strict)
    return {**metrics, "verdict": verdict_label,
            "verdict_evaluation": {"applicable": True, "reason": None, "label": verdict_label}}


def _quote_checks(row, supports, context, required):
    if "quote_checks" not in row:
        if required:
            raise ValueError(f"{context}: missing quote_checks for the v4 protocol")
        return
    checks = _indexed(row["quote_checks"], "citation_id", f"{context} quote checks", row["citation_ids"])
    if row["label"] in ("ENTAILMENT", "CONTRADICTION") and len(checks) == 1 and next(iter(checks.values()))["label"] != row["label"]:
        raise ValueError(f"{context}: decisive label disagrees with its only quote check")
    for sid, check in checks.items():
        label = _decision(check, NLI_LABELS, f"{context}.{sid}")
        quote = check.get("quote")
        excerpt = supports[sid].get("excerpt") or ""
        if not isinstance(quote, str) or (excerpt.strip() and not quote.strip()):
            raise ValueError(f"{context}: quote check needs original text")
        if quote and normalize_excerpt(quote) not in normalize_excerpt(excerpt):
            raise ValueError(f"{context}: quote check is outside its own citation excerpt")
        if label in ("ENTAILMENT", "CONTRADICTION") and not quote.strip():
            raise ValueError(f"{context}: decisive quote check has no evidence")
        if (row["label"] == "ENTAILMENT" and label == "CONTRADICTION"
                or row["label"] == "CONTRADICTION" and label == "ENTAILMENT"):
            raise ValueError(f"{context}: decisive label conflicts with its quote checks")


def _claim_links(row, finding_reviews, required):
    if "claim_links" not in row:
        if required:
            raise ValueError("Coverage is missing claim_links for the v4 protocol")
        return
    links = row["claim_links"]
    if not isinstance(links, list) or (row["label"] == "MISSING" and links):
        raise ValueError("MISSING coverage requires empty claim_links")
    if row["label"] != "MISSING" and not links:
        raise ValueError("Reviewed coverage must identify actual atomic claims")
    if row["label"] in ("ENTAILMENT", "CONTRADICTION") and len(links) == 1 and links[0]["label"] != row["label"]:
        raise ValueError("Decisive coverage disagrees with its only atomic claim link")
    seen, owned = set(), set()
    for link in links:
        fid, index = link.get("finding_id"), link.get("claim_index")
        if (fid not in row["finding_ids"] or type(index) is not int or index < 0
                or index >= len(finding_reviews[fid]["claims"]) or (fid, index) in seen):
            raise ValueError("Coverage claim_links contain an ineligible or duplicate atomic claim")
        seen.add((fid, index))
        label = _decision(link, NLI_LABELS, "coverage claim link")
        if row["label"] == "ENTAILMENT" and label == "CONTRADICTION":
            raise ValueError("Entailed coverage links an incompatible returned claim")
        owned.update(finding_reviews[fid]["claims"][index]["citation_ids"])
    if row["label"] in ("ENTAILMENT", "CONTRADICTION") and set(row["citation_ids"]) - owned:
        raise ValueError("Coverage borrows citations not reviewed for its linked atomic claims")


def score_retrieval_facts(reference, passages, judgments):
    """Count supported reference facts independently of a graph or abstention."""
    if not isinstance(passages, dict):
        raise ValueError("captured passages must be an object")
    for pid, passage in passages.items():
        _text(pid, "passage ID")
        if not isinstance(passage, dict):
            raise ValueError(f"{pid}: expected a captured passage")
        _text(passage.get("excerpt"), f"{pid}.excerpt")
    facts = _indexed(reference.get("facts"), "id", "reference facts")
    decisions = _indexed(judgments, "gold_id", "capture fact judgments", facts)
    supported = 0
    for gid, row in decisions.items():
        label = _decision(row, NLI_LABELS, gid)
        ids = _citations(row, label, passages, gid)
        supported += label == "ENTAILMENT" and all(_source_url({"source": passages[id_]}) for id_ in ids)
    return _ratio(supported, len(facts))


def score_capture(reference, passages, capture_judgment):
    """Score only the verified retrieved passages available when a graph was not produced."""
    if not isinstance(capture_judgment, dict):
        raise ValueError("capture judgment must be an object")
    coverage = score_retrieval_facts(reference, passages, capture_judgment.get("facts"))
    abstention = _decision(capture_judgment.get("abstention"),
                           ("JUSTIFIED", "UNNECESSARY", "INDETERMINATE"), "abstention")
    return {"retrieved_reference_coverage": coverage, "abstention": abstention}


def _metric(value):
    percent = f" ({value['rate']:.0%})" if value["rate"] is not None else " (N/A)"
    return f"{value['numerator']}/{value['denominator']}{percent}"


def _ids(items):
    return ", ".join(items) if items else "none"


def _workbook_rows(entry):
    return ", ".join(f"{ref['sheet']} row {ref['row']}" for ref in entry.get("source_refs", []))


def format_capture_report(reference, passages, capture_judgment, score, *, visible_coverage=None):
    """Show what the research step captured and why the judge accepted or questioned abstention."""
    facts = {item["id"]: item for item in reference["facts"]}
    lines = [
        f"## Retrieval diagnostic · {reference['case_id']}", "", reference["query"], "",
        f"- Gold fact coverage in retrieved passages: {_metric(score['retrieved_reference_coverage'])}",
        f"- Gold fact coverage in user-visible findings: {_metric(visible_coverage or _ratio(0, len(facts)))}",
        f"- Assessment of the system's abstention: **{score['abstention']}**. "
        f"{capture_judgment['abstention']['reason']}",
        "", "### Gold facts and retrieved evidence", "",
    ]
    for row in capture_judgment["facts"]:
        gid = row["gold_id"]
        lines += [f"- **{gid} · {row['label']}** — {facts[gid]['text']}",
                  f"  {row['reason']} (passages: {_ids(row['citation_ids'])})."]
        refs = _workbook_rows(facts[gid])
        if refs:
            lines.append(f"  Workbook: {refs}.")
    lines.extend(["", "### Captured passages", ""])
    for pid, passage in passages.items():
        title = passage.get("title") or "Source"
        url = _source_url({"source": passage})
        if url:
            title = f"[{title}](<{url}>)"
        lines += [f"**{pid}** · {title}", "", "> " + passage["excerpt"].replace("\n", "\n> "), ""]
    return "\n".join(lines)


def format_case_report(reference, prediction, judgment, score):
    """Render replayable scoring results with the finding and source IDs used by the judge."""
    lines = [
        f"# TraceGraph evaluation · {reference['case_id']}", "", reference["query"], "",
        f"- Commitment identity: {score['identity']}. {judgment['identity']['reason']}",
        f"- Gold fact coverage across the attempt: {_metric(score['reference_coverage'])}",
        f"- Supported atomic system claims: {_metric(score['finding_support'])}",
        f"- Supported atomic system claims traced to quoted passages with source URLs: "
        f"{_metric(score['traceability'])}",
        "", "## Gold fact coverage", "",
    ]
    facts = {x["id"]: x for x in reference["facts"]}
    for row in judgment["coverage"]:
        gid = row["gold_id"]
        refs = _workbook_rows(facts[gid])
        lines += [f"- **{gid} · {row['label']}** — {facts[gid]['text']}",
                  f"  {row['reason']} (findings: {_ids(row['finding_ids'])}, "
                  f"citations: {_ids(row['citation_ids'])})."]
        if refs:
            lines.append(f"  Workbook: {refs}.")
    for source in reference.get("sources", []):
        url = _source_url({"source": source})
        if url:
            lines.extend(["", f"{source['sheet']} row {source['row']} · [{source['title']}](<{url}>)"])

    lines.extend(["", "## Findings and quoted passages", ""])
    findings = {x["id"]: x for x in prediction["findings"]}
    for row in judgment["findings"]:
        finding = findings[row["finding_id"]]
        lines += [f"### {finding['id']}", "", finding["claim"], ""]
        for atom in row["claims"]:
            lines.append(f"- **{atom['label']}** — {atom['claim']} {atom['reason']} "
                         f"(citations: {_ids(atom['citation_ids'])}).")
        for support in finding.get("supports", []):
            source = support.get("source") or {}
            title = source.get("title") or source.get("citation") or "Source"
            url = _source_url(support)
            if url:
                title = f"[{title}](<{url}>)"
            locator = support.get("locator") or "location unspecified"
            lines.extend(["", f"**{support['id']}** · {title} · {locator}", "",
                          "> " + (support.get("excerpt") or "(no excerpt)").replace("\n", "\n> ")])
        lines.append("")

    lines.extend(["", "## Delivery conditions and verdict", ""])
    conditions = {x["id"]: x for x in reference["conditions"]}
    for row in (judgment.get("verdict") or {}).get("conditions", []):
        cid = row["condition_id"]
        lines += [f"- **{cid} · {row['label']}** — {conditions[cid]['text']}",
                  f"  {row['reason']} (citations: {_ids(row['citation_ids'])})."]
    assessment = prediction.get("assessment") or {}
    lines.extend(["", f"System verdict: **{assessment.get('label') or 'unavailable'}**. "
                  f"{assessment.get('verdict') or ''}",
                  "", f"Judge: **{score['verdict'] or 'N/A'}**. "
                  f"{(judgment.get('verdict') or {}).get('reason') or 'No displayed verdict to evaluate.'}", ""])
    reference_verdict = reference.get("reference_verdict") or {}
    if reference_verdict.get("status"):
        lines.extend([f"Workbook assessment: {reference_verdict['status']} "
                      f"{reference_verdict.get('reasoning') or ''}", ""])
    return "\n".join(lines)
