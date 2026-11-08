"""Reconcile fresh observations with a commitment's persistent evidence graph."""

from collections import defaultdict, deque
from copy import deepcopy
from uuid import uuid4

LABELS = (
    "CORROBORATED", "PARTIALLY_CORROBORATED", "UNVERIFIED",
    "NOT_YET_DUE", "CONFLICTING", "REFUTED",
)


class ReconciliationError(RuntimeError):
    """The provider returned a reconciliation that cannot be safely saved."""


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationError("Reconciliation omitted a required text field")
    return value.strip()


def _claim_key(claim):
    return " ".join(_text(claim).casefold().split())


def _support_key(support):
    source = support["source"]
    source_ref = source.get("url") or (
        source.get("title"), source.get("citation"), source.get("publisher")
    )
    return source_ref, support.get("locator") or "", support.get("excerpt") or support.get("summary")


def _observations(findings, supports, *, from_review=False):
    known = {_support_key(support) for finding in findings for support in finding["supports"]}
    observations = []
    for support in supports:
        key = _support_key(support)
        if key not in known:
            known.add(key)
            observations.append({
                "id": f"o{len(observations) + 1}",
                "support": support,
                "from_review": from_review,
            })
    return observations


def _relation_key(kind, from_id, to_id):
    if kind == "CONFLICT":
        from_id, to_id = sorted((from_id, to_id))
    return kind, from_id, to_id


def _unresolved(relation):
    return relation["kind"] == "CONFLICT" and relation["status"] == "UNRESOLVED"


def _assert_acyclic(findings, relations):
    edges = defaultdict(list)
    indegree = dict.fromkeys(findings, 0)
    for relation in relations:
        if relation["kind"] == "PROGRESS":
            parent, child = relation["from_id"], relation["to_id"]
            edges[parent].append(child)
            indegree[child] += 1
    queue = deque(finding_id for finding_id, count in indegree.items() if not count)
    visited = 0
    while queue:
        visited += 1
        for child in edges[queue.popleft()]:
            indegree[child] -= 1
            if not indegree[child]:
                queue.append(child)
    if visited != len(findings):
        raise ReconciliationError("Progress links would form a cycle")


class EvidenceReconciler:
    """Run one review at most for each cache shared across a job's retries."""

    def __init__(self, judge, *, followup=None, assessor=None):
        self._judge = judge
        self._followup = followup
        self._assessor = assessor

    def reconcile(self, context, snapshot, result, *, followup_cache=None):
        cache = {} if followup_cache is None else followup_cache
        revision = snapshot["revision"]
        if type(revision) is not int or revision < 0:
            raise ReconciliationError("Expected a nonnegative evidence revision")
        existing_conflicts = {
            _relation_key(edge["kind"], edge["from_id"], edge["to_id"])
            for edge in snapshot["relations"] if edge["kind"] == "CONFLICT"
        }
        state = self._apply(context, snapshot, result["evidence"], [], cache)
        pending = [
            edge for edge in state["relations"] if _unresolved(edge)
        ]
        if self._followup is not None and "reviewed_pairs" not in cache:
            pairs = self._pairs(state, pending, existing_conflicts)
            if pairs:
                cache["reviewed_pairs"] = pairs
                cache["review_supports"] = self._followup(context, deepcopy(pairs))
                if not isinstance(cache["review_supports"], list):
                    raise ReconciliationError("Conflict review did not return an evidence list")
        if cache.get("review_supports"):
            pending_keys = {_relation_key("CONFLICT", edge["from_id"], edge["to_id"]) for edge in pending}
            reviewed = [
                pair for pair in cache["reviewed_pairs"]
                if _relation_key("CONFLICT", pair["from_id"], pair["to_id"]) in pending_keys
            ]
            state = self._apply(context, state, cache["review_supports"], reviewed, cache, from_review=True)

        assessment = self._assessor(context, deepcopy(state)) if self._assessor else state["assessment"]
        assessment = _assessment(assessment, state["relations"])
        return {
            "expected_revision": revision,
            "verification_date": result["verification_date"],
            **assessment,
            "findings": state["findings"],
            "relations": state["relations"],
        }

    @staticmethod
    def _pairs(state, pending, existing_conflicts):
        claims = {finding["id"]: finding["claim"] for finding in state["findings"]}
        return [
            {
                "from_id": edge["from_id"], "to_id": edge["to_id"],
                "from_claim": claims[edge["from_id"]], "to_claim": claims[edge["to_id"]],
                "reasoning": edge["reasoning"],
            }
            for edge in pending
            if _relation_key("CONFLICT", edge["from_id"], edge["to_id"]) not in existing_conflicts
        ]

    def _apply(self, context, snapshot, supports, reviewed, cache, *, from_review=False):
        observations = _observations(snapshot["findings"], supports, from_review=from_review)
        if from_review and not observations:
            return snapshot
        plan = self._judge(context, deepcopy(snapshot), deepcopy(observations), deepcopy(reviewed))
        if not isinstance(plan, dict) or not all(
            isinstance(plan.get(key), list) for key in ("assignments", "relations")
        ):
            raise ReconciliationError("Expected a reconciliation plan with assignments and relations")

        findings = {finding["id"]: deepcopy(finding) for finding in snapshot["findings"]}
        aliases, assignments = _apply_assignments(
            findings, observations, plan["assignments"], cache.setdefault("finding_ids", {})
        )
        relations = _apply_relations(
            findings, aliases, assignments, snapshot["relations"], plan["relations"], reviewed
        )
        _assert_acyclic(findings, relations)
        return {
            "revision": snapshot["revision"],
            "findings": list(findings.values()),
            "relations": relations,
            "assessment": plan.get("assessment"),
        }


def _apply_assignments(findings, observations, assignments, allocated_ids):
    available = {observation["id"]: observation for observation in observations}
    claims = {_claim_key(finding["claim"]): finding_id for finding_id, finding in findings.items()}
    aliases = {}
    attached = {}
    for assignment in assignments:
        observation_id = _text(assignment.get("observation_id"))
        if observation_id not in available or observation_id in attached:
            raise ReconciliationError("An observation ID is unknown or assigned more than once")
        ref = _text(assignment.get("finding_id"))
        if ref in findings:
            finding_id = ref
        elif ref.startswith("new:"):
            claim = _text(assignment.get("claim"))
            key = _claim_key(claim)
            if key not in claims:
                finding_id = allocated_ids.setdefault(key, f"finding-{uuid4()}")
                findings[finding_id] = {"id": finding_id, "claim": claim, "supports": []}
                claims[key] = finding_id
            finding_id = claims[key]
            if ref in aliases and aliases[ref] != finding_id:
                raise ReconciliationError("A new finding alias refers to more than one claim")
            aliases[ref] = finding_id
        else:
            raise ReconciliationError("Assignment refers to an unknown finding")
        observation = available[observation_id]
        findings[finding_id]["supports"].append(deepcopy(observation["support"]))
        attached[observation_id] = (finding_id, observation)
    if attached.keys() != available.keys():
        raise ReconciliationError("Every new observation must be assigned to a finding")
    return aliases, attached


def _apply_relations(findings, aliases, assignments, previous, proposed, reviewed):
    relations = {
        _relation_key(edge["kind"], edge["from_id"], edge["to_id"]): deepcopy(edge)
        for edge in previous
    }
    reviewed_keys = {
        _relation_key("CONFLICT", pair["from_id"], pair["to_id"]) for pair in reviewed
    }

    def resolve_id(ref):
        ref = _text(ref)
        finding_id = aliases.get(ref, ref)
        if finding_id not in findings:
            raise ReconciliationError("A link refers to an unknown finding")
        return finding_id

    seen = set()
    for proposed_edge in proposed:
        kind = proposed_edge.get("kind")
        from_id = resolve_id(proposed_edge.get("from_id"))
        to_id = resolve_id(proposed_edge.get("to_id"))
        if kind not in ("PROGRESS", "CONFLICT") or from_id == to_id:
            raise ReconciliationError("Invalid relation between evidence findings")
        key = _relation_key(kind, from_id, to_id)
        if key in seen:
            raise ReconciliationError("The provider proposed a relation more than once")
        seen.add(key)
        status, preferred_ref = proposed_edge.get("status"), proposed_edge.get("preferred_id")
        reason = _text(proposed_edge.get("reasoning"))
        preferred = None
        current = relations.get(key)
        if kind == "PROGRESS":
            if status is not None or preferred_ref is not None:
                raise ReconciliationError("A progress link cannot declare a conflict outcome")
        elif status == "UNRESOLVED" and preferred_ref is None:
            pass
        elif status == "RESOLVED":
            preferred = resolve_id(preferred_ref)
            if preferred not in (from_id, to_id):
                raise ReconciliationError("The preferred finding must be a side of the conflict")
            if current and current["status"] == "RESOLVED" and current["preferred_id"] == preferred:
                continue
            if key not in reviewed_keys or not current or current["status"] != "UNRESOLVED":
                raise ReconciliationError("A conflict can only be resolved after targeted review")
            citations = _review_citations(proposed_edge.get("support_ids"), assignments, preferred)
            reason = f'{current["reasoning"]} Review: {reason} Sources: {", ".join(citations)}.'
            from_id, to_id = current["from_id"], current["to_id"]
        else:
            raise ReconciliationError("A conflict must be unresolved or have a preferred finding")
        if current is None or status == "RESOLVED":
            relations[key] = {
                "kind": kind, "from_id": from_id, "to_id": to_id,
                "reasoning": reason, "status": status, "preferred_id": preferred,
            }
    return list(relations.values())


def _review_citations(ids, assignments, preferred):
    if not isinstance(ids, list) or not ids:
        raise ReconciliationError("Resolving a conflict requires cited review observations")
    citations = []
    for observation_id in ids:
        if not isinstance(observation_id, str) or observation_id not in assignments:
            raise ReconciliationError("The resolution cites an unknown review observation")
        finding_id, observation = assignments[observation_id]
        if finding_id != preferred or not observation["from_review"]:
            raise ReconciliationError("A resolution citation must support the preferred finding")
        source = observation["support"]["source"]
        citation = source.get("url") or source.get("citation") or source.get("title")
        if not isinstance(citation, str) or not citation.strip():
            raise ReconciliationError("The review observation lacks a citable source")
        if citation not in citations:
            citations.append(citation)
    return citations


def _assessment(value, relations):
    if not isinstance(value, dict) or value.get("label") not in LABELS:
        raise ReconciliationError("Expected a valid aggregate assessment label")
    verdict, reasoning = _text(value.get("verdict")), _text(value.get("reasoning"))
    has_unresolved = any(_unresolved(edge) for edge in relations)
    if not has_unresolved and value["label"] == "CONFLICTING":
        raise ReconciliationError("A conflicting assessment requires an unresolved conflict link")
    return {"label": value["label"], "verdict": verdict, "reasoning": reasoning}
