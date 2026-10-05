"""Offline HTTP boundary, clock, cache, and vendor fixtures for Task 6."""
from datetime import date, datetime, timedelta, timezone

import httpx

from investment_ai.config import ProviderPolicy, load_api_key
from investment_ai.market.calendar import Session, SessionCalendar

NOW = datetime(2025, 1, 4, 13, tzinfo=timezone.utc)
FLOOR = datetime(2020, 1, 1, tzinfo=timezone.utc)
DAY = date(2025, 1, 2)
SECRET = "test-only-sentinel-secret"
POLICY = ProviderPolicy("https://api.massive.com", 2, 13, 4, 8)
META = dict(ticker="AAPL", composite_figi="BBG000B9XRY4", primary_exchange="XNAS",
            currency_name="usd", type="CS", active=True, locale="us", market="stocks")
BAR = dict(t=1735794000000, o=100, h=103, l=99, c=102, v=12000)
SPLIT = dict(ticker="AAPL", execution_date="2025-01-02", split_from=1, split_to=4,
             id="split-id", adjustment_type="forward_split", historical_adjustment_factor=0.01)
DIVIDEND = dict(ticker="AAPL", ex_dividend_date="2025-01-02", cash_amount=0.26,
                currency="USD", declaration_date="2024-12-20", record_date="2025-01-02",
                pay_date="2025-01-10", split_adjusted_cash_amount=0.01)


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def now(self):
        return NOW + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        assert seconds >= 0
        self.elapsed += seconds


class Cache:
    def __init__(self):
        self.records = {}

    def get(self, key, not_before):
        item = self.records.get(key)
        return item if item and item.fetched_at >= not_before else None

    def put(self, key, response):
        self.records[key] = response


def transport(handler, *, policy=POLICY, cache=None, clock=None, jitter=lambda: 0.0):
    from investment_ai.providers.transport import JsonTransport
    clock, cache = clock or Clock(), cache or Cache()
    requests, starts = [], []

    def respond(request):
        requests.append(request)
        starts.append(clock.monotonic())
        return handler(request, len(requests))

    client = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True)
    key = load_api_key({"MASSIVE_API_KEY": SECRET})
    return JsonTransport(client, key, policy, clock, cache, jitter), requests, starts


def response(results=None, **extra):
    return httpx.Response(200, json=dict(status="OK", results=results or [], **extra))


def vendor(request, count):
    path = request.url.path
    if path.startswith("/v3/"):
        return response(META)
    if path.startswith("/v2/"):
        return response([BAR], adjusted=False, ticker="AAPL", resultsCount=1)
    return response([SPLIT] if path.endswith("splits") else [DIVIDEND])


def provider(handler=vendor, **kwargs):
    from investment_ai.providers.massive import MassiveProvider
    connection, requests, starts = transport(handler, **kwargs)
    calendar = SessionCalendar(frozen_sessions=(Session(
        DAY, datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc),
        datetime(2025, 1, 2, 21, tzinfo=timezone.utc)),))
    return MassiveProvider(connection, calendar), requests, starts


def fetch(source, **changes):
    args = dict(symbol="AAPL", start=DAY, end=DAY, observed_at=NOW,
                availability_basis="reconstructed_history", actions_start=DAY,
                cache_not_before=FLOOR)
    return source.fetch(**(args | changes))
