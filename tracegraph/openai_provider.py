"""OpenAI Responses transport and verified public web evidence."""

import json
import os
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from tracegraph.experimental_prompt import _STOP_REASONS
from tracegraph.providers import (
    ClaudeWebResearcher, ProviderError, _MAX_RESPONSE_BYTES, _NoRedirect, _SEARCH_SYSTEM,
    _TEXT, _json_user, _object,
)
from tracegraph.public_excerpt import load_public_text, normalize_excerpt
from tracegraph.source_passages import recover_passages


_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
_MAX_PASSAGES = 12
_MAX_QUOTE_LENGTH = 700
_SEARCH_OUTPUT = _object({
    "passages": {"type": "array", "items": _object({"url": _TEXT, "quote": _TEXT})},
    "stop_reason": {"type": "string", "enum": ["", *sorted(_STOP_REASONS)]},
})
_QUOTE_INSTRUCTIONS = """
Return only the requested JSON instead of a prose brief. In passages, propose at most
12 literal excerpts that are directly relevant to the target. Use a URL from the tool
results and a contiguous quote from that page, under 700 characters. Use the source's
own words, including the relevant terms, scope, or date. Do not paraphrase, combine
separate spans, insert ellipses, or include a citation inside the quote. Include the
original promise where available. Also include separate literal excerpts for any
reported outcome, completion date, or cancellation found in the search.
Prefer a short sentence from the body of the source over a search-result summary.
Leave passages empty if there are no usable results.
Put the requested STOP_REASON value in stop_reason, or use an empty string if the
research instructions do not request it.
""".strip()


def _strict_format(schema):
    return {"format": {"type": "json_schema", "name": "tracegraph", "strict": True, "schema": schema}}


def _output_parts(document):
    if document.get("status") != "completed":
        raise ProviderError("OpenAI did not complete the response")
    parts = []
    for item in document["output"]:
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise ProviderError("OpenAI returned malformed message content")
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in {"output_text", "refusal"}:
                raise ProviderError("OpenAI returned unexpected message content")
            if part["type"] == "refusal":
                raise ProviderError("OpenAI refused to complete the response")
            if not isinstance(part.get("text"), str):
                raise ProviderError("OpenAI returned malformed output text")
            parts.append(part)
    if not parts:
        raise ProviderError("OpenAI returned no output text")
    return parts


class IncompleteWebSearch(ProviderError):
    """No usable source could be attributed to a completed search action."""


class OpenAIClient:
    """Injectable OpenAI Responses API client."""

    def __init__(
        self, api_key=None, model=None, *, timeout=120, opener=None, emit=None, deadline=None,
        allow_web_search=True, reasoning_effort=None,
    ):
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not isinstance(key, str) or not key.strip():
            raise ProviderError("OPENAI_API_KEY is required for OpenAI")
        self._api_key = key.strip()
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self._timeout = timeout
        self._open = opener or build_opener(_NoRedirect()).open
        self._emit = emit
        self._deadline = deadline
        self._allow_web_search = allow_web_search
        self._reasoning_effort = reasoning_effort

    def _timeout_for(self, limit):
        timeout = limit if self._deadline is None else min(limit, self._deadline - monotonic())
        if timeout <= 0:
            raise ProviderError("The research job reached its deadline")
        return timeout

    def _request(self, *, system, messages, max_tokens, **options):
        payload = {
            "model": self.model, "instructions": system, "input": messages,
            "max_output_tokens": max_tokens, "store": False, **options,
        }
        request = Request(
            _RESPONSES_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = monotonic()
        timeout = self._timeout_for(self._timeout)
        document = {}
        request_id = None
        status = "invalid_response"
        try:
            with self._open(request, timeout=timeout) as response:
                headers = getattr(response, "headers", None)
                request_id = headers.get("x-request-id") if headers is not None else None
                body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ProviderError("OpenAI Responses output exceeded its size limit")
            decoded = json.loads(body)
            if not isinstance(decoded, dict) or decoded.get("object") != "response":
                raise ProviderError("OpenAI returned an invalid Responses object")
            output = decoded.get("output")
            if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
                raise ProviderError("OpenAI returned malformed response output")
            document = decoded
            status = "ok"
        except HTTPError as error:
            status = f"http_{error.code}"
            request_id = error.headers.get("x-request-id") if error.headers is not None else None
            raise ProviderError(f"OpenAI Responses returned HTTP {error.code}") from None
        except (URLError, OSError):
            status = "transport_error"
            raise ProviderError("OpenAI Responses request or response failed") from None
        except ValueError:
            raise ProviderError("OpenAI returned invalid Responses JSON") from None
        finally:
            if self._emit is not None:
                self._emit({
                    "type": "provider_call", "provider": "openai", "model": self.model, "status": status,
                    "seconds": monotonic() - started, "request_id": request_id or document.get("id"),
                    "usage": document.get("usage"), "search_enabled": bool(options.get("tools")),
                    "web_search_ids": [
                        item["id"] for item in document.get("output", [])
                        if item.get("type") == "web_search_call" and isinstance(item.get("id"), str)
                    ],
                    "stop_reason": document.get("status"),
                    **({"web_search_statuses": [item.get("status") for item in document.get("output", [])
                                                if item.get("type") == "web_search_call"]}
                       if options.get("tools") else {}),
                })
        return document

    def create(self, *, system, messages, max_tokens, output_config):
        """Provide the text-block shape used by the shared structured adapter."""
        options = {"reasoning": {"effort": self._reasoning_effort}} if self._reasoning_effort else {}
        document = self._request(
            system=system, messages=messages, max_tokens=max_tokens,
            text=_strict_format(output_config["format"]["schema"]), **options,
        )
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": part["text"]} for part in _output_parts(document)],
        }

    def search(self, request, *, system, max_uses):
        if not self._allow_web_search:
            raise ProviderError("Provider tools are disabled during fixed-evidence replay")
        document = self._request(
            system=f"{system}\n\n{_QUOTE_INSTRUCTIONS}",
            messages=[_json_user(request)], max_tokens=4096,
            tools=[{"type": "web_search"}], tool_choice="required", max_tool_calls=max_uses,
            include=["web_search_call.action.sources"], text=_strict_format(_SEARCH_OUTPUT),
        )
        parts = _output_parts(document)
        sources, reported_results = _search_sources(document["output"], parts)
        try:
            proposed = json.loads("".join(part["text"] for part in parts))
        except ValueError:
            raise ProviderError("OpenAI returned malformed evidence proposals") from None
        if not isinstance(proposed, dict) or set(proposed) != set(_SEARCH_OUTPUT["required"]):
            raise ProviderError("OpenAI returned unexpected evidence proposals")
        rows, stop = proposed["passages"], proposed["stop_reason"]
        if not isinstance(rows, list) or not isinstance(stop, str) or stop not in {"", *_STOP_REASONS}:
            raise ProviderError("OpenAI returned invalid evidence proposals")
        if not rows and reported_results is None and not sources:
            raise ProviderError("OpenAI did not report whether the search returned sources")
        return rows, stop, sources


def _source_url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            return None
        return parts._replace(fragment="").geturl()
    except ValueError:
        return None


def _search_sources(output, parts):
    calls = [item for item in output if item.get("type") == "web_search_call"]
    completed = [call for call in calls if call.get("status") == "completed"]
    if not completed:
        raise IncompleteWebSearch("OpenAI web search did not complete")
    mixed = len(completed) != len(calls)
    sources = {}
    reported_results = None
    for call in completed:
        action = call.get("action")
        if not isinstance(action, dict):
            raise ProviderError("OpenAI returned a malformed web search action")
        if action.get("type") in {"open_page", "find_in_page"} and (url := _source_url(action.get("url"))):
            sources.setdefault(url, url)
        listed = action.get("sources")
        if not isinstance(listed, list):
            continue
        reported_results = bool(listed) or bool(reported_results)
        for source in listed:
            if isinstance(source, dict) and (url := _source_url(source.get("url"))):
                sources.setdefault(url, url)
    for part in parts:
        annotations = part.get("annotations", [])
        if not isinstance(annotations, list):
            raise ProviderError("OpenAI returned malformed web search citations")
        for citation in annotations:
            if not isinstance(citation, dict) or citation.get("type") != "url_citation":
                continue
            url = _source_url(citation.get("url"))
            title = citation.get("title")
            # Message annotations carry no tool-call ID. In a mixed response
            # only an independently listed, completed action can authorize a URL.
            if url and (not mixed or url in sources):
                sources[url] = title.strip() if isinstance(title, str) and title.strip() else url
    if mixed and not sources:
        raise IncompleteWebSearch("OpenAI web search did not complete with attributable sources")
    return sources, reported_results


class OpenAIWebResearcher(ClaudeWebResearcher):
    """Reuse research and reconciliation inputs with a verified OpenAI capture."""

    def __init__(self, client, *, clock=None, search_strategy=None, emit=None, source_loader=None, search_client=None,
                 allow_source_recovery=True, evidence_synthesizer=None, evidence_followup=True):
        super().__init__(client, clock=clock, search_strategy=search_strategy, emit=emit,
                         source_loader=source_loader, allow_source_recovery=allow_source_recovery,
                         evidence_synthesizer=evidence_synthesizer, evidence_followup=evidence_followup)
        self._search_client = search_client or client
        self._load_source = source_loader or load_public_text

    def locate_documents(self, request, *, system, max_uses):
        proposals, _, sources = self._search_client.search(request, system=system, max_uses=max_uses)
        return proposals, sources

    def _search(self, request, *, system=_SEARCH_SYSTEM, max_uses=6):
        max_uses = self._claim_search_uses(max_uses)
        if not max_uses:
            return "STOP_REASON=BUDGET", {}
        try:
            rows, stop, sources = self._search_client.search(request, system=system, max_uses=max_uses)
        except IncompleteWebSearch:
            # A DISCOVER retry consumes its existing reserve. The second call
            # is deliberately outside this handler and can never re-enter it.
            if not self._budget_discovery or not self._allow_source_recovery or not self._remaining_search_uses:
                raise
            remaining = self._claim_search_uses(min(max_uses, self._remaining_search_uses))
            self._emit({"type": "search_retry", "reason": "incomplete_web_search", "allocated": remaining})
            rows, stop, sources = self._search_client.search(request, system=system, max_uses=remaining)
        self._located_sources.update(sources)
        pages = {}
        citations = {}
        seen = set()
        for row in rows[:_MAX_PASSAGES]:
            if not isinstance(row, dict) or not isinstance(row.get("quote"), str):
                continue
            url = _source_url(row.get("url"))
            excerpt = normalize_excerpt(row["quote"])
            if url not in sources or not 12 <= len(excerpt) <= _MAX_QUOTE_LENGTH or (url, excerpt) in seen:
                continue
            if url not in pages:
                try:
                    pages[url] = self._load_source(url, timeout=self._search_client._timeout_for(8))
                except (OSError, ValueError):
                    pages[url] = None
            page = pages[url]
            if not isinstance(page, str) or (start := page.find(excerpt)) < 0:
                self._emit({"type": "source_validation", "url": url,
                            "status": "FETCH_UNAVAILABLE" if not page else "QUOTE_NOT_FOUND"})
                continue
            verified = page[start:start + len(excerpt)]
            seen.add((url, excerpt))
            citations[f"c{len(citations) + 1}"] = {
                "url": url, "title": sources[url], "excerpt": verified, "context": verified,
            }
        if (sources and not citations and request.get("kind") == "DISCOVER"
                and "evidence_gap" not in request and self._allow_source_recovery):
            if not any(pages.values()):
                for url in sources:
                    if self._source_reads >= 2:
                        break
                    if url in pages or url in self._attempted_source_urls:
                        continue
                    self._attempted_source_urls.add(url)
                    self._source_reads += 1
                    try:
                        pages[url] = self._load_source(url, timeout=self._search_client._timeout_for(8))
                    except (OSError, ValueError, ProviderError):
                        continue
            recovered = recover_passages(self._client, request.get("target"), pages, sources, rows[:_MAX_PASSAGES])
            citations = {f"c{i}": citation for i, citation in enumerate(recovered, 1)}
            self._emit({"type": "source_span_recovery", "readable_pages": sum(bool(p) for p in pages.values()),
                        "verified_count": len(citations)})
        if sources and not citations:
            self._emit({"type": "source_validation", "status": "NO_VERIFIED_EXCERPTS", "located_sources": len(sources)})
        lines = [f"[{key}] {citation['excerpt']}" for key, citation in citations.items()]
        if stop:
            lines.append(f"STOP_REASON={stop}")
        return "\n".join(lines), citations
