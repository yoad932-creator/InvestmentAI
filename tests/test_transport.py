"""Request-policy tests exercise real transport with fake HTTP and time."""
from dataclasses import replace
from datetime import timedelta
from email.utils import format_datetime
import logging
import traceback

import httpx
import pytest

from investment_ai.errors import ProviderError
from provider_support import Cache, Clock, FLOOR, NOW, POLICY, SECRET, response, transport

PATH = "/stocks/v1/splits"


def test_request_spacing():
    def handler(request, count):
        if count == 2:
            return httpx.Response(503)
        return response([{"page": count}], **({"next_url":
            f"https://api.massive.com{PATH}?cursor={count}"} if count < 6 else {}))
    connection, requests, starts = transport(handler)
    pages = list(connection.pages(PATH, {}, FLOOR))
    assert len(pages) == 5
    assert starts == [0, 13, 26, 39, 52, 65]
    assert all(r.extensions["timeout"] == dict(connect=10, read=30, write=30, pool=30)
               for r in requests)


@pytest.mark.parametrize("header", ["30", format_datetime(NOW + timedelta(seconds=30), usegmt=True)])
def test_429_retry_after(header):
    connection, requests, starts = transport(lambda *_: httpx.Response(429, headers={"Retry-After": header}))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "retry_exhausted"
    assert len(requests) == 4
    assert starts[1] >= 30
    assert all(b - a >= 13 for a, b in zip(starts, starts[1:]))


def test_retry_after_ceiling():
    connection, requests, _ = transport(lambda *_: httpx.Response(429, headers={"Retry-After": "121"}))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "retry_later"
    assert len(requests) == 1


@pytest.mark.parametrize("status", [301, 401, 403, 404, 408, 501])
def test_no_retry(status):
    connection, requests, _ = transport(lambda *_: httpx.Response(status, headers={"Location": "https://evil.test/"}))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "http_error"
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [500, 502, 503, 504, "connect", "read"])
def test_allowed_retry(failure):
    def handler(request, count):
        if count > 1:
            return response([{"ok": True}])
        if isinstance(failure, int):
            return httpx.Response(failure)
        exception = httpx.ConnectTimeout if failure == "connect" else httpx.ReadTimeout
        raise exception(SECRET, request=request)
    connection, requests, starts = transport(handler)
    assert list(connection.pages(PATH, {}, FLOOR))[0].payload["results"] == [{"ok": True}]
    assert starts == [0, 13]


def test_attempt_cap_and_backoff_after_slow_request():
    clock = Clock()
    def handler(*_):
        clock.sleep(20)
        return httpx.Response(500)
    connection, requests, starts = transport(handler, clock=clock, policy=replace(POLICY, attempts=9), jitter=lambda: 1)
    with pytest.raises(ProviderError):
        list(connection.pages(PATH, {}, FLOOR))
    assert starts == [0, 22, 45, 70]
    assert len(requests) == 4


@pytest.mark.parametrize("payload", [None, [], {"status": "ERROR"}, {"status": "OK"},
                                     {"status": "OK", "results": "bad"}])
def test_malformed_response(payload):
    connection, requests, _ = transport(lambda *_: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "invalid_response"
    assert len(requests) == 1


def test_malformed_json():
    connection, requests, _ = transport(lambda *_: httpx.Response(200, content=b"{broken"))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "invalid_response"
    assert len(requests) == 1


@pytest.mark.parametrize("url", ["http://api.massive.com/stocks/v1/splits?cursor=2",
    "https://evil.test/stocks/v1/splits?cursor=2", "https://api.massive.com/stocks/v1/dividends?cursor=2",
    "https://user@api.massive.com/stocks/v1/splits?cursor=2", "https://api.massive.com:444/stocks/v1/splits?cursor=2"])
def test_unsafe_pagination(url):
    connection, requests, _ = transport(lambda *_: response(next_url=url))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "invalid_pagination"
    assert len(requests) == 1


def test_repeated_cursor():
    connection, requests, _ = transport(lambda *_: response(next_url=f"https://api.massive.com{PATH}?cursor=same"))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "invalid_pagination"
    assert len(requests) == 2


def test_page_limit():
    connection, requests, _ = transport(lambda r, n: response(next_url=f"https://api.massive.com{PATH}?cursor={n}"))
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "invalid_pagination"
    assert len(requests) == 100


def test_secrets_redacted_and_pagination(caplog):
    caplog.set_level(logging.DEBUG)
    cache = Cache()
    def handler(request, count):
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        assert SECRET not in str(request.url)
        return response([{"page": count}], **({"next_url":
            f"https://api.massive.com{PATH}?cursor=2&apiKey={SECRET}"} if count == 1 else {}))
    connection, requests, _ = transport(handler, cache=cache)
    pages = list(connection.pages(PATH, {"ticker": "AAPL"}, FLOOR))
    assert [p.payload["results"][0]["page"] for p in pages] == [1, 2]
    assert SECRET not in repr(cache.records) + caplog.text


def test_timeout_exception_redacted():
    def handler(request, count):
        raise httpx.ReadTimeout(SECRET, request=request)
    connection, requests, _ = transport(handler)
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "retry_exhausted"
    assert SECRET not in "".join(traceback.format_exception(error.value))
    assert len(requests) == 4


def test_cache_hit_no_network_and_refresh():
    connection, requests, _ = transport(lambda *_: response([{"v": 1}]))
    first = list(connection.pages(PATH, {"b": "2", "a": "1"}, FLOOR))
    second = list(connection.pages(PATH, {"a": "1", "b": "2"}, FLOOR))
    assert first == second and len(requests) == 1
    third = list(connection.pages(PATH, {"a": "1", "b": "2"}, NOW + timedelta(seconds=1)))
    assert len(requests) == 2
    assert third[0].request_hash == first[0].request_hash
    assert third[0].fetched_at > first[0].fetched_at


@pytest.mark.parametrize("body", [b'{"status":"OK","results":[{"v":NaN}]}',
    b'{"status":"OK","results":[{"v":Infinity}]}',
    b'{"status":"ERROR","status":"OK","results":[]}'])
def test_invalid_json_values_never_cached(body):
    cache = Cache()
    connection, requests, _ = transport(lambda *_: httpx.Response(200, content=body), cache=cache)
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "invalid_response"
    assert len(requests) == 1 and not cache.records


def test_unsafe_initial_path_does_not_send_secret(caplog):
    caplog.set_level(logging.DEBUG)
    connection, requests, _ = transport(lambda *_: response())
    with pytest.raises(ProviderError) as error:
        list(connection.pages(f"/stocks/v1/{SECRET}", {}, FLOOR))
    assert error.value.code == "invalid_request"
    assert requests == [] and SECRET not in caplog.text


def test_non_timeout_network_error_not_retried():
    def handler(request, count):
        raise httpx.ConnectError(SECRET, request=request)
    connection, requests, _ = transport(handler)
    with pytest.raises(ProviderError) as error:
        list(connection.pages(PATH, {}, FLOOR))
    assert error.value.code == "transport_error"
    assert len(requests) == 1
    assert SECRET not in "".join(traceback.format_exception(error.value))


def test_limiter_never_catches_up_after_idle():
    clock = Clock()
    connection, _, starts = transport(lambda *_: response(), clock=clock)
    list(connection.pages(PATH, {"ticker": "AAPL"}, FLOOR))
    clock.sleep(100)
    list(connection.pages(PATH, {"ticker": "MSFT"}, FLOOR))
    list(connection.pages(PATH, {"ticker": "SPY"}, FLOOR))
    assert starts == [0, 100, 113]


def test_decimal_payload_preserves_all_digits():
    connection, _, _ = transport(lambda *_: httpx.Response(200, content=
        b'{"status":"OK","results":[{"amount":0.12345678901234567890123456789}]}'))
    page = list(connection.pages(PATH, {}, FLOOR))[0]
    assert str(page.payload["results"][0]["amount"]) == "0.12345678901234567890123456789"
