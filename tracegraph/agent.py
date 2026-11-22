"""Lookup and research orchestration for the TraceGraph API and worker."""

from datetime import date, datetime
from logging import getLogger
from urllib.parse import urlsplit

from tracegraph.public_excerpt import normalize_excerpt


_DISCOVERED_FIELDS = (
    "description",
    "organization_id",
    "organization_name",
    "responsible_entity_raw",
    "beneficiary",
    "obligation_type",
    "amount_value",
    "term_periodicity",
    "geographic_scope",
    "condition_contingency",
    "milestone",
    "original_promise",
)
_MAX_RESEARCH_WRITE_ATTEMPTS = 3
LOG = getLogger(__name__)


class ResolutionUnavailable(RuntimeError):
    """The retrieval service could not complete a valid identity decision."""


class ResearchUnavailable(RuntimeError):
    """The research provider or a concurrent catalog update prevented completion."""


class Agent:
    """Use injected retrieval, persistence, and evidence provider adapters."""

    def __init__(self, retrieval, research_store, researcher, reconciler=None, *, companion_research=None):
        self._retrieval = retrieval
        self._store = research_store
        self._researcher = researcher
        self._reconciler = reconciler
        self._companion_research = companion_research

    def lookup(self, query):
        """Decide lookup routing. The API owns idempotency and task admission."""
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("query must be a non-blank string of at most 4000 characters")
        query = query.strip()
        decision = self._resolve({"query": query})
        if decision["outcome"] == "NO_MATCH":
            result = {"outcome": "START_DISCOVERY", "query": query}
            if decision.get("suggestions"):
                result["suggestions"] = decision["suggestions"]
            return result
        return decision

    def execute(self, claimed_job):
        """Execute one claimed task, writing its outcome through ResearchStore."""
        claim = {key: claimed_job[key] for key in ("job_id", "claim_token")}
        try:
            match claimed_job["kind"]:
                case "DISCOVER":
                    self._discover(claim, claimed_job)
                case "REFRESH":
                    self._refresh(claim, claimed_job)
                case _:
                    raise ValueError("Unknown research job kind")
        except ResolutionUnavailable:
            error = _error("RESOLUTION_UNAVAILABLE", "The identity check could not be completed.")
        except ResearchUnavailable:
            LOG.info("Research task could not complete", exc_info=True)
            error = _error("RESEARCH_UNAVAILABLE", "The research could not be completed.")
        except Exception:
            error = _error("INTERNAL_ERROR", "The research job could not be saved or executed.", retryable=False)
        else:
            return

        # If a claim expired or persistence is down, let the worker handle the
        # store failure. A stale worker cannot write a new terminal result.
        self._store.fail(claim, error)

    def _resolve(self, identity):
        try:
            decision = self._retrieval.resolve(identity)
            outcome = decision["outcome"]
            fields = {
                "MATCH": "commitment_id",
                "AMBIGUOUS": "question",
                "NO_MATCH": "catalog_revision",
            }
            field = fields[outcome]
            if _has_text(decision[field]):
                result = {"outcome": outcome, field: decision[field]}
                if outcome != "MATCH" and decision.get("suggestions"):
                    result["suggestions"] = decision["suggestions"]
                return result
        except Exception as exc:
            raise ResolutionUnavailable("Retrieval failed to resolve the identity") from exc
        raise ResolutionUnavailable("Retrieval returned an incomplete identity decision")

    def _research(self, claimed_job, context):
        try:
            response = self._researcher(claimed_job, context)
        except Exception as exc:
            raise ResearchUnavailable("The evidence provider failed") from exc
        if not isinstance(response, dict):
            raise ResearchUnavailable("The evidence provider returned an invalid response")
        return response

    def _discover(self, claim, claimed_job):
        response = self._research(claimed_job, None)
        if "draft" not in response:
            raise ResearchUnavailable("The discovery response is missing its draft decision")
        draft = response["draft"]
        if draft is not None and not isinstance(draft, dict):
            raise ResearchUnavailable("The discovery response contains an invalid draft")
        if response.get("target_status") == "AMBIGUOUS":
            question = response.get("clarification_question")
            if not _has_text(question):
                raise ResearchUnavailable("An ambiguous research target requires an object-selection question")
            result = {"outcome": "AMBIGUOUS", "question": question.strip()}
            suggestions = _web_leads(response.get("related_evidence", []))
            if suggestions:
                result["suggestions"] = suggestions
            self._store.finish_without_run(claim, result)
            return
        missing_scope = _scope_parts(response, "missing_scope")
        if missing_scope or draft is None or not _has_cited_passage(draft.get("original_promise")):
            gaps = _scope_parts(response, "gaps") or missing_scope or [
                "A source documenting the complete requested original obligation is still needed."]
            located = _web_leads(response.get("related_evidence", []), limit=32)
            result = {
                "outcome": "PARTIAL_RESEARCH", "query": claimed_job["query"],
                "message": "Research is incomplete; no commitment was saved.",
                "facts": [{"source_title": row["title"], "source_url": row["source_url"],
                           "excerpt": row["description"]} for row in located],
                "gaps": gaps,
            }
            unmatched = _web_leads(response.get("unmatched_evidence", []), limit=32)
            if unmatched:
                result["unmatched_records"] = [{"source_title": row["title"], "source_url": row["source_url"],
                                                "excerpt": row["description"]} for row in unmatched]
            suggestions = _web_leads(response.get("related_evidence", []))
            if len(suggestions) < 3:
                quoted_urls = {row["source_url"] for row in suggestions}
                for source in response.get("related_sources", []):
                    url = source.get("source_url")
                    if not _has_text(url) or url in quoted_urls:
                        continue
                    candidate = _web_leads([{"source": {"url": url, "title": source.get("title") or url},
                                             "excerpt": "Located source; no excerpt could be verified."}])
                    if candidate:
                        suggestions.append({**candidate[0], "excerpt": ""})
                        quoted_urls.add(url)
                    if len(suggestions) == 3:
                        break
            if suggestions:
                result["suggestions"] = suggestions
            self._store.finish_without_run(claim, result)
            return

        if not all(_has_text(draft.get(field)) for field in ("description", "title", "original_text")):
            raise ResearchUnavailable("The discovery draft is missing commitment details")

        criteria = _scope_parts(response, "criteria")
        unmatched = _web_leads(response.get("unmatched_evidence", []), limit=32)
        retained_records = ([{"source_title": row["title"], "source_url": row["source_url"],
                              "excerpt": row["description"]} for row in unmatched])
        identity = {field: draft.get(field) for field in _DISCOVERED_FIELDS}
        new_commitment = None
        for _ in range(_MAX_RESEARCH_WRITE_ATTEMPTS):
            decision = self._resolve({"discovered": identity})
            match decision["outcome"]:
                case "MATCH":
                    self._store.finish_without_run(claim, {
                        "outcome": "RESOLVED",
                        "commitment_id": decision["commitment_id"],
                        "run_id": None,
                        **({"unmatched_records": retained_records} if retained_records else {}),
                    })
                    return
                case "AMBIGUOUS":
                    self._store.finish_without_run(claim, decision)
                    return

            if new_commitment is None:
                result = _validate_result(response.get("result"))
                if retained_records:
                    result = {**result, "unmatched_records": retained_records}
                if self._reconciler is not None:
                    context = {
                        "commitment": {**identity, "title": draft["title"]},
                        "organization": None,
                    }
                    if criteria:
                        context["requested_query"] = claimed_job["query"]
                        context["requested_criteria"] = criteria
                    snapshot = {"revision": 0, "findings": [], "relations": [], "assessment": None}
                    result = self._reconcile(context, snapshot, result, {})
                try:
                    embedding = self._retrieval.embed_commitment(identity["description"])
                except Exception as exc:
                    raise ResolutionUnavailable("Commitment embedding failed") from exc
                new_commitment = {
                    **identity,
                    "title": draft["title"],
                    "original_text": draft["original_text"],
                    "notes": draft.get("notes"),
                    "embedding": embedding,
                }

            saved = self._store.finish_discovery(
                claim, decision["catalog_revision"], new_commitment, result
            )
            if saved["outcome"] == "COMMITTED":
                return
            if saved["outcome"] != "RECHECK_REQUIRED":
                raise ValueError("Unexpected discovery completion outcome")

        raise ResearchUnavailable("The catalog changed during each discovery write attempt")

    def _refresh(self, claim, claimed_job):
        commitment_id = claimed_job["commitment_id"]
        context = self._store.load_commitment(commitment_id)
        if context is None:
            raise ValueError("The refresh target no longer exists")
        response = self._research(claimed_job, context)
        result = _validate_result(response.get("result"))
        if self._reconciler is None:
            self._store.finish_refresh(claim, result)
            return

        followup_cache = {}
        companions = []
        for attempt in range(_MAX_RESEARCH_WRITE_ATTEMPTS):
            snapshot = self._store.research_snapshot(commitment_id)
            if snapshot is None:
                raise ValueError("The refresh target no longer exists")
            if attempt == 0 and self._companion_research is not None:
                try:
                    candidates = self._companion_research(context, snapshot, result)
                    if not isinstance(candidates, list):
                        raise ResearchUnavailable("Companion research returned an invalid evidence list")
                    companions = [item for item in candidates if _valid_evidence(item) and _has_cited_passage(item)]
                except Exception:
                    LOG.warning("Companion document research failed; continuing the ordinary refresh.", exc_info=True)
            combined = _with_companions(snapshot, result, companions)
            try:
                enriched = self._reconcile(context, snapshot, combined, followup_cache)
            except ResearchUnavailable:
                if combined is result:
                    raise
                LOG.warning("Companion evidence could not be reconciled; retrying the ordinary refresh.", exc_info=True)
                companions = []
                # A failed plan may already have spent this job's conflict search.
                # Discard its supports and decisions, retaining only that budget marker.
                followup_cache = {"reviewed_pairs": []} if "reviewed_pairs" in followup_cache else {}
                enriched = self._reconcile(context, snapshot, result, followup_cache)
            saved = self._store.finish_refresh(claim, enriched)
            if saved["outcome"] == "COMMITTED":
                return
            if saved["outcome"] != "RECHECK_REQUIRED":
                raise ValueError("Unexpected refresh completion outcome")
        raise ResearchUnavailable("The evidence graph changed during each refresh write attempt")

    def _reconcile(self, context, snapshot, result, followup_cache):
        try:
            updates = self._reconciler.reconcile(
                context, snapshot, result, followup_cache=followup_cache
            )
            return {**result, **updates, "expected_revision": snapshot["revision"]}
        except Exception as exc:
            raise ResearchUnavailable("Evidence reconciliation failed") from exc


def _error(code, message, *, retryable=True):
    return {"code": code, "message": message, "retryable": retryable}


def _with_companions(snapshot, result, companions):
    if not companions:
        return result

    def passage_key(item):
        source = item["source"]
        url = source.get("url")
        reference = url.partition("#")[0] if _has_text(url) else source.get("citation")
        excerpt = item.get("excerpt")
        return reference, normalize_excerpt(excerpt) if isinstance(excerpt, str) else ""

    # A companion may locate an already captured passage at a different character offset.
    # Recheck against the current revision if a concurrent refresh saved it first.
    seen = {passage_key(item) for finding in snapshot["findings"] for item in finding["supports"]}
    seen.update(passage_key(item) for item in result["evidence"])
    new = []
    for item in companions:
        key = passage_key(item)
        if key not in seen:
            new.append(item)
            seen.add(key)
    return {**result, "evidence": [*result["evidence"], *new]} if new else result


def _has_text(value):
    return isinstance(value, str) and bool(value.strip())


def _scope_parts(response, field):
    parts = response.get(field, [])
    if not isinstance(parts, list) or not all(_has_text(part) for part in parts):
        raise ResearchUnavailable("The discovery response contains invalid scope details")
    return [part.strip() for part in parts]


def _web_leads(evidence, *, limit=3):
    leads = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source = item.get("source", {})
        if not isinstance(source, dict):
            continue
        url, excerpt = source.get("url"), item.get("excerpt")
        if not _has_text(url) or not _has_text(excerpt):
            continue
        key = url, excerpt.strip()
        if key in leads:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username is not None:
                continue
        except ValueError:
            continue
        title = source.get("title") or url
        if _has_text(title):
            leads[key] = {"kind": "WEB_LEAD", "title": title.strip(), "source_title": title.strip(),
                          "description": excerpt.strip(), "excerpt": excerpt.strip(),
                          "source_url": url}
        if len(leads) == limit:
            break
    return list(leads.values())


def _has_source_reference(source):
    if not isinstance(source, dict) or not _has_text(source.get("title")):
        return False
    if _has_text(source.get("citation")):
        return True
    url = source.get("url")
    if not _has_text(url):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def _has_cited_passage(passage):
    return (
        isinstance(passage, dict)
        and _has_source_reference(passage.get("source"))
        and _has_text(passage.get("locator"))
        and _has_text(passage.get("excerpt"))
    )


def _is_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def _is_timestamp(value):
    try:
        timestamp = datetime.fromisoformat(value)
        return value[10:11] in ("T", "t") and timestamp.utcoffset() is not None
    except (TypeError, ValueError):
        return False


def _valid_evidence(item):
    return (
        isinstance(item, dict)
        and _has_source_reference(item.get("source"))
        and _has_text(item.get("status"))
        and _has_text(item.get("explanation"))
        and (_has_text(item.get("excerpt")) or _has_text(item.get("summary")))
        and _is_timestamp(item.get("retrieved_at"))
    )


def _validate_result(result):
    if (
        not isinstance(result, dict)
        or not _is_date(result.get("verification_date"))
        or not _has_text(result.get("verdict"))
        or not _has_text(result.get("reasoning"))
        or not isinstance(result.get("evidence"), list)
    ):
        raise ResearchUnavailable("The evidence provider returned an invalid research result")
    if not all(_valid_evidence(evidence) for evidence in result["evidence"]):
        raise ResearchUnavailable("The research result has incomplete source evidence")
    return result
