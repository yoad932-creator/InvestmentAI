"""Bounded synchronous JSON acquisition; persistence is injected, not owned."""
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
import json
import math
import re
from threading import Lock
from typing import Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

from investment_ai.config import ProviderPolicy, load_api_key
from investment_ai.domain.provenance import canonical_hash, require_utc
from investment_ai.errors import ProviderError


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True)
class CachedResponse:
    payload: Mapping[str, object]
    fetched_at: datetime
    request_hash: str


class RequestCache(Protocol):
    def get(self, key: str, not_before: datetime) -> CachedResponse | None: ...
    def put(self, key: str, response: CachedResponse) -> None: ...


_SECRET_FIELDS = {"apikey", "api_key", "authorization", "token", "access_token"}


def _json_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return result


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON number")


class JsonTransport:
    """Share one instance per run: limiter covers every endpoint, page and retry.

    The caller owns the HTTP client and its lifetime. No HTTP bodies, URLs,
    credentials or third-party exception messages are logged by this class.
    """

    def __init__(self, client: httpx.Client, api_key: str, policy: ProviderPolicy,
                 clock: Clock, cache: RequestCache, jitter: Callable[[], float]):
        self.client, self.policy, self.clock, self.cache = client, policy, clock, cache
        self._api_key = load_api_key({"MASSIVE_API_KEY": api_key})
        self._jitter, self._last_start, self._lock = jitter, None, Lock()

    def _url(self, path, params, original=None):
        url = urlsplit(path if "://" in path else self.policy.base_url.rstrip("/") + path)
        if self._api_key in unquote(url.path):
            raise ProviderError("invalid_request", "Credentials cannot appear in request paths.")
        base = urlsplit(self.policy.base_url)
        if (url.scheme != "https" or url.netloc != base.netloc or url.username
                or url.password or url.fragment):
            raise ProviderError("invalid_pagination", "Unsafe request destination.")
        if original and url.path != original:
            # Aggregate continuations may replace only the start date with milliseconds.
            prefix, _, end = original.rpartition("/")
            prefix, _, _start = prefix.rpartition("/")
            candidate = url.path.split("/")
            if not (original.startswith("/v2/aggs/ticker/")
                    and url.path.startswith(prefix + "/") and candidate[-1] == end
                    and len(candidate) == len(original.split("/"))
                    and re.fullmatch(r"[0-9]+|[0-9]{4}-[0-9]{2}-[0-9]{2}", candidate[-2])):
                raise ProviderError("invalid_pagination", "Continuation changed endpoint.")
        pairs = parse_qsl(url.query, keep_blank_values=True) + list(params.items())
        clean = [(key, value) for key, value in pairs if key.lower() not in _SECRET_FIELDS]
        if any(self._api_key in str(value) for pair in clean for value in pair):
            raise ProviderError("invalid_request", "Credentials cannot appear in request parameters.")
        return urlunsplit((url.scheme, url.netloc, url.path, urlencode(sorted(clean)), ""))

    def _redact(self, value):
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()
                    if key.lower() not in _SECRET_FIELDS}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            return value.replace(self._api_key, "[redacted]")
        return value

    def _retry_after(self, value):
        if value is None:
            return 0.0
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - self.clock.now()).total_seconds()
            except (TypeError, ValueError, OverflowError):
                raise ProviderError("invalid_response", "Invalid Retry-After header.") from None
        if not math.isfinite(seconds) or seconds > 120:
            raise ProviderError("retry_later", "Provider requires a later resumable run.")
        return max(0.0, seconds)

    def _request(self, url):
        with self._lock:
            retry_delay = 0.0
            for attempt in range(min(self.policy.attempts, 4)):
                gap = max(13.0, self.policy.request_interval_seconds)
                spacing = 0 if self._last_start is None else self._last_start + gap - self.clock.monotonic()
                self.clock.sleep(max(0.0, spacing, retry_delay))
                self._last_start = self.clock.monotonic()
                try:
                    request = httpx.Request("GET", url, headers={"Authorization": f"Bearer {self._api_key}"},
                                            extensions={"timeout": dict(connect=10, read=30, write=30, pool=30)})
                    response = self.client.send(request, auth=None, follow_redirects=False)
                except httpx.TimeoutException:
                    response = None
                except httpx.HTTPError:
                    raise ProviderError("transport_error", "Market-data transport failed.") from None
                if response is not None and response.status_code == 200:
                    try:
                        return json.loads(response.content, parse_float=Decimal,
                                          parse_constant=_invalid_constant, object_pairs_hook=_json_object)
                    except (ValueError, UnicodeError):
                        raise ProviderError("invalid_response", "Malformed JSON response.") from None
                if response is not None and response.status_code not in (429, 500, 502, 503, 504):
                    raise ProviderError("http_error", "Market-data HTTP request rejected.")
                server_delay = self._retry_after(response.headers.get("Retry-After")) if response is not None else 0
                if attempt + 1 == min(self.policy.attempts, 4):
                    raise ProviderError("retry_exhausted", "Market-data retry budget exhausted.")
                jitter = self._jitter()
                if not math.isfinite(jitter) or not 0 <= jitter <= 1:
                    raise ProviderError("invalid_request", "Jitter must be between zero and one.")
                retry_delay = max(2 ** attempt + jitter, server_delay)

    def pages(self, path: str, params: Mapping[str, str], not_before: datetime) -> Iterator[CachedResponse]:
        require_utc(not_before, "cache freshness floor")
        url = self._url(path, params)
        original, seen = urlsplit(url).path, set()
        for _ in range(100):
            key = canonical_hash({"provider": "massive", "url": url})
            if key in seen:
                raise ProviderError("invalid_pagination", "Repeated continuation.")
            seen.add(key)
            cached = self.cache.get(key, not_before)
            if cached is None:
                payload = self._request(url)
                fetched_at = self.clock.now()
            else:
                payload, fetched_at = dict(cached.payload), cached.fetched_at
            if not isinstance(payload, dict) or payload.get("status") != "OK":
                raise ProviderError("invalid_response", "Provider did not return a successful response.")
            if "results" not in payload and original.startswith("/v2/aggs/") and payload.get("resultsCount") == 0:
                payload = payload | {"results": []}
            if not isinstance(payload.get("results"), (list, dict)):
                raise ProviderError("invalid_response", "Missing or malformed results.")
            continuation = payload.get("next_url")
            if continuation is not None:
                if not isinstance(continuation, str) or not continuation:
                    raise ProviderError("invalid_pagination", "Malformed continuation.")
                continuation = self._url(continuation, {}, original)
                payload = payload | {"next_url": continuation}
            cleaned = self._redact(payload)
            result = CachedResponse(cleaned, fetched_at, key)
            if cached is None:
                self.cache.put(key, result)
            yield result
            if continuation is None:
                return
            url = continuation
        raise ProviderError("invalid_pagination", "Exceeded the 100-page request bound.")
