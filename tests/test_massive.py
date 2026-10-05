"""Massive fixtures use the documented REST fields, never a live account."""
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from investment_ai.domain.actions import Dividend, Split
from investment_ai.domain.bars import DailyBar
from investment_ai.errors import ProviderError
from provider_support import BAR, DAY, DIVIDEND, FLOOR, META, NOW, SPLIT, Clock, fetch, provider, response, vendor


def test_raw_daily_request_and_canonical_output():
    source, requests, _ = provider()
    batch = fetch(source)
    assert source.capabilities().history_years == 2
    assert batch.actions_complete and isinstance(batch.bars[0], DailyBar)
    assert requests[0].url.path == "/v3/reference/tickers/AAPL"
    assert dict(requests[0].url.params) == {"date": "2025-01-02"}
    assert requests[1].url.path == "/v2/aggs/ticker/AAPL/range/1/day/2025-01-02/2025-01-02"
    assert dict(requests[1].url.params) == {"adjusted": "false", "sort": "asc", "limit": "50000"}
    assert batch.instrument.currency == "USD" and batch.instrument.kind == "stock"
    assert batch.bars[0].close == Decimal("102")
    assert "historical_adjustment_factor" not in repr(asdict(batch))
    assert len(batch.request_hashes) == 4


def test_metadata_and_actions():
    source, requests, _ = provider()
    batch = fetch(source, actions_start=date(2024, 12, 1))
    split, dividend = batch.actions
    assert isinstance(split, Split) and (split.split_from, split.split_to) == (Decimal(1), Decimal(4))
    assert isinstance(dividend, Dividend) and dividend.cash_amount == Decimal("0.26")
    assert dividend.ex_date == DAY and dividend.pay_date == date(2025, 1, 10)
    assert dividend.declaration_date == date(2024, 12, 20) and dividend.record_date == DAY
    assert dict(requests[2].url.params) == dict(ticker="AAPL", limit="5000",
        sort="execution_date.asc", **{"execution_date.gte": "2024-12-01", "execution_date.lte": "2025-01-02"})
    assert requests[3].url.path == "/stocks/v1/dividends"
    assert requests[3].url.params["ex_dividend_date.lte"] == "2025-01-02"


def test_timestamp_mapping_and_first_seen():
    source, _, _ = provider()
    bar = fetch(source, availability_basis="observed").bars[0]
    assert bar.session == DAY
    assert bar.source_start == datetime(2025, 1, 2, 5, tzinfo=timezone.utc)
    assert bar.source_end == datetime(2025, 1, 3, 5, tzinfo=timezone.utc)
    assert bar.provenance.ingested_at == NOW + timedelta(seconds=13)
    assert bar.provenance.available_at == bar.provenance.ingested_at
    historical = fetch(source).bars[0]
    assert historical.provenance.available_at == datetime(2025, 1, 3, 13, tzinfo=timezone.utc)
    assert historical.provenance.source_revision == bar.provenance.source_revision


@pytest.mark.parametrize("adjusted", [True, None, "false"])
def test_adjusted_response_rejected(adjusted):
    def handler(request, count):
        return response([BAR], adjusted=adjusted, ticker="AAPL") if count == 2 else vendor(request, count)
    source, _, _ = provider(handler)
    with pytest.raises(ProviderError) as error:
        fetch(source)
    assert error.value.code == "invalid_response"


def test_out_of_range_history():
    source, requests, _ = provider()
    with pytest.raises(ProviderError) as error:
        fetch(source, actions_start=date(2023, 1, 3))
    assert error.value.code == "history_not_entitled"
    assert requests == []


def test_no_results():
    def handler(request, count):
        if count == 1:
            return vendor(request, count)
        if count == 2:
            return httpx.Response(200, json=dict(status="OK", adjusted=False, ticker="AAPL", resultsCount=0))
        return response([])
    source, _, _ = provider(handler)
    batch = fetch(source)
    assert batch.bars == batch.actions == () and batch.actions_complete


@pytest.mark.parametrize("stream", ["splits", "dividends"])
def test_action_failure_never_returns_partial_batch(stream):
    def handler(request, count):
        return httpx.Response(403) if request.url.path.endswith(stream) else vendor(request, count)
    source, _, _ = provider(handler)
    with pytest.raises(ProviderError) as error:
        fetch(source)
    assert error.value.code == "http_error"


@pytest.mark.parametrize("kind,field,value", [("metadata", "composite_figi", None),
    ("metadata", "currency_name", None), ("metadata", "ticker", "MSFT"),
    ("bar", "v", "NaN"), ("bar", "c", True), ("bar", "t", 0),
    ("split", "split_from", 0), ("split", "ticker", "MSFT"),
    ("dividend", "cash_amount", None), ("dividend", "ex_dividend_date", "2025-01-03")])
def test_malformed_records_fail_safely(kind, field, value):
    def handler(request, count):
        kinds = {1: ("metadata", META), 2: ("bar", BAR), 3: ("split", SPLIT), 4: ("dividend", DIVIDEND)}
        label, record = kinds[count]
        if kind != label:
            return vendor(request, count)
        record = record | {field: value}
        return response(record if count == 1 else [record], **({"adjusted": False, "ticker": "AAPL"} if count == 2 else {}))
    source, _, _ = provider(handler)
    with pytest.raises(ProviderError):
        fetch(source)


def test_cached_rerun_and_metadata_actions_expire():
    clock = Clock()
    source, requests, _ = provider(clock=clock)
    first = fetch(source)
    assert fetch(source) == first and len(requests) == 4
    clock.sleep(86401)
    second = fetch(source)
    assert len(requests) == 7  # Raw historical bars reused; metadata and actions refreshed.
    assert second.bars == first.bars
    assert second.instrument.provenance.ingested_at > first.instrument.provenance.ingested_at


def test_second_page_failure_and_resume():
    fail = True
    def handler(request, count):
        if request.url.path.endswith("splits"):
            if "cursor" not in request.url.params:
                return response([SPLIT], next_url="https://api.massive.com/stocks/v1/splits?cursor=2")
            return httpx.Response(403) if fail else response([])
        return vendor(request, count)
    source, requests, _ = provider(handler)
    with pytest.raises(ProviderError):
        fetch(source)
    fail = False
    batch = fetch(source)
    assert len(batch.actions) == 2 and batch.actions_complete
    assert len(requests) == 6  # Three successful cached pages survive the failed acquisition.


def test_source_revision_tracks_content_not_action_id():
    cash = 0.26
    def handler(request, count):
        return response([DIVIDEND | {"cash_amount": cash, "id": "same-event"}]) if request.url.path.endswith("dividends") else vendor(request, count)
    source, _, _ = provider(handler)
    first = fetch(source)
    cash = 0.3
    second = fetch(source, cache_not_before=NOW + timedelta(seconds=40))
    assert first.actions[1].provenance.source_revision != second.actions[1].provenance.source_revision


def test_daily_pagination_keeps_both_inclusive_boundaries():
    from investment_ai.market.calendar import Session, SessionCalendar
    end = date(2025, 1, 3)
    def handler(request, count):
        if request.url.path.startswith("/v2/"):
            if "cursor" in request.url.params:
                return response([BAR | {"t": 1735880400000}], adjusted=False, ticker="AAPL")
            return response([BAR], adjusted=False, ticker="AAPL", next_url=
                "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/1735880400000/2025-01-03?cursor=2")
        return vendor(request, count)
    source, requests, starts = provider(handler)
    source.calendar = SessionCalendar(frozen_sessions=source.calendar.export(DAY, DAY) + (Session(
        end, datetime(2025, 1, 3, 14, 30, tzinfo=timezone.utc), datetime(2025, 1, 3, 21, tzinfo=timezone.utc)),))
    batch = fetch(source, end=end)
    assert [bar.session for bar in batch.bars] == [DAY, end]
    assert len(requests) == 5 and starts == [0, 13, 26, 39, 52]


@pytest.mark.parametrize("payload", [{"status": "OK", "results": [BAR], "adjusted": False,
    "ticker": "AAPL", "resultsCount": 2}, {"status": "OK", "results": [None], "adjusted": False, "ticker": "AAPL"}])
def test_incomplete_bar_page_rejected(payload):
    source, _, _ = provider(lambda request, n: httpx.Response(200, json=payload) if n == 2 else vendor(request, n))
    with pytest.raises(ProviderError) as error:
        fetch(source)
    assert error.value.code == "invalid_response"
