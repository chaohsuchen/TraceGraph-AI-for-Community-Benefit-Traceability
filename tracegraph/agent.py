"""Lookup and research orchestration for the TraceGraph API and worker."""

from datetime import date, datetime
from urllib.parse import urlsplit


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


class ResolutionUnavailable(RuntimeError):
    """The retrieval service could not complete a valid identity decision."""


class ResearchUnavailable(RuntimeError):
    """The research provider or a concurrent catalog update prevented completion."""


class Agent:
    """Use injected retrieval, persistence, and evidence provider adapters."""

    def __init__(self, retrieval, research_store, researcher, reconciler=None):
        self._retrieval = retrieval
        self._store = research_store
        self._researcher = researcher
        self._reconciler = reconciler

    def lookup(self, query):
        """Decide lookup routing. The API owns idempotency and task admission."""
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("query must be a non-blank string of at most 4000 characters")
        query = query.strip()
        decision = self._resolve({"query": query})
        if decision["outcome"] == "NO_MATCH":
            return {"outcome": "START_DISCOVERY", "query": query}
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
                return {"outcome": outcome, field: decision[field]}
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
        if draft is None or not _has_original_promise(draft.get("original_promise")):
            self._store.finish_without_run(claim, {
                "outcome": "NO_PUBLIC_PROMISE",
                "message": "No publicly documented original promise could be located.",
            })
            return

        if not all(_has_text(draft.get(field)) for field in ("description", "title", "original_text")):
            raise ResearchUnavailable("The discovery draft is missing commitment details")

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
                    })
                    return
                case "AMBIGUOUS":
                    self._store.finish_without_run(claim, decision)
                    return

            if new_commitment is None:
                result = _validate_result(response.get("result"))
                if self._reconciler is not None:
                    context = {
                        "commitment": {**identity, "title": draft["title"]},
                        "organization": None,
                    }
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
        for _ in range(_MAX_RESEARCH_WRITE_ATTEMPTS):
            snapshot = self._store.research_snapshot(commitment_id)
            if snapshot is None:
                raise ValueError("The refresh target no longer exists")
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


def _has_text(value):
    return isinstance(value, str) and bool(value.strip())


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


def _has_original_promise(promise):
    return (
        isinstance(promise, dict)
        and _has_source_reference(promise.get("source"))
        and _has_text(promise.get("locator"))
        and _has_text(promise.get("excerpt"))
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


def _validate_result(result):
    if (
        not isinstance(result, dict)
        or not _is_date(result.get("verification_date"))
        or not _has_text(result.get("verdict"))
        or not _has_text(result.get("reasoning"))
        or not isinstance(result.get("evidence"), list)
    ):
        raise ResearchUnavailable("The evidence provider returned an invalid research result")
    for evidence in result["evidence"]:
        if (
            not isinstance(evidence, dict)
            or not _has_source_reference(evidence.get("source"))
            or not _has_text(evidence.get("status"))
            or not _has_text(evidence.get("explanation"))
            or not (_has_text(evidence.get("excerpt")) or _has_text(evidence.get("summary")))
            or not _is_timestamp(evidence.get("retrieved_at"))
        ):
            raise ResearchUnavailable("The research result has incomplete source evidence")
    return result