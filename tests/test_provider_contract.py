"""Offline behavior checks shared by interchangeable daily providers."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from investment_ai.domain.actions import Dividend, Split
from investment_ai.domain.instruments import Instrument
from investment_ai.errors import ProviderError
from investment_ai.providers.base import (
    ProviderBatch, ProviderCapabilities, fetch_daily, require_daily_capabilities,
)
from support import RecordsProvider, RowsProvider, daily_bar, provenance


@pytest.fixture
def instrument():
    return Instrument("fixture-instrument-1", "AAPL", "XNAS", "USD", "stock", True, provenance())


@pytest.fixture
def records():
    return tuple(daily_bar(session=date(2025, 1, day), close=Decimal(str(100 + day)))
                 for day in (1, 2, 3, 4))


@pytest.fixture
def actions():
    return (
        Split("fixture-instrument-1", date(2025, 1, 1), Decimal("1"), Decimal("2"), provenance()),
        Dividend("fixture-instrument-1", date(2025, 1, 3), Decimal("0.50"), "USD",
                 None, None, None, provenance()),
    )


def fetch(provider, **overrides):
    request = dict(symbol="AAPL", start=date(2025, 1, 2), end=date(2025, 1, 3),
                   observed_at=datetime(2025, 1, 4, 13, tzinfo=timezone.utc),
                   availability_basis="reconstructed_history", actions_start=date(2025, 1, 1),
                   cache_not_before=datetime(2025, 1, 1, tzinfo=timezone.utc))
    return fetch_daily(provider, **(request | overrides))


def test_provider_substitution(instrument, records, actions):
    first = RecordsProvider(instrument, records, actions)
    second = RowsProvider(instrument, tuple((bar.session, str(bar.close)) for bar in records), actions)
    left, right = fetch(first), fetch(second)
    assert isinstance(left, ProviderBatch) and isinstance(right, ProviderBatch)
    assert left == right
    # A consumer needs only the canonical result, never provider transport fields.
    assert sum(bar.close for bar in left.bars) == Decimal("205")
    assert sum(bar.close for bar in right.bars) == Decimal("205")
    assert set(asdict(left)) == {"instrument", "bars", "actions", "request_hashes", "actions_complete"}


@pytest.mark.parametrize("stream", ["splits", "dividends"])
def test_partial_result_is_error(instrument, records, actions, stream):
    provider = RecordsProvider(instrument, records, actions, failed_stream=stream)
    with pytest.raises(ProviderError) as error:
        fetch(provider)
    assert error.value.code == "incomplete_batch"


@pytest.mark.parametrize("flag", ["daily", "raw_bars", "splits", "dividends"])
def test_unsupported_capabilities(instrument, records, flag):
    provider = RecordsProvider(instrument, records, (), capabilities_override={flag: False})
    with pytest.raises(ProviderError) as error:
        fetch(provider)
    assert error.value.code == "unsupported_capabilities"
    assert provider.calls == 0


def test_supported_capabilities():
    capabilities = ProviderCapabilities("fixture", True, True, True, True, 2)
    assert capabilities is not None
    assert require_daily_capabilities(capabilities) is None
    assert capabilities.history_years == 2
    with pytest.raises(FrozenInstanceError):
        capabilities.history_years = 5


@pytest.mark.parametrize("history", [0, -1, True, 2.5])
def test_invalid_history_capability(history):
    with pytest.raises(ProviderError):
        ProviderCapabilities("fixture", True, True, True, True, history)


def test_non_boolean_capability_rejected():
    with pytest.raises(ProviderError):
        ProviderCapabilities("fixture", "yes", True, True, True, 2)


def test_inclusive_range_contract(instrument, records, actions):
    result = fetch(RecordsProvider(instrument, records, actions))
    assert result is not None
    assert tuple(bar.session for bar in result.bars) == (date(2025, 1, 2), date(2025, 1, 3))
    assert result.actions == actions
    assert result.bars[0].provenance.available_at == provenance().available_at


def test_single_day_and_empty_range_results(instrument, records):
    provider = RecordsProvider(instrument, records, ())
    single = fetch(provider, start=date(2025, 1, 3), end=date(2025, 1, 3))
    assert single is not None and len(single.bars) == 1
    empty = fetch(provider, start=date(2025, 1, 5), end=date(2025, 1, 5))
    assert empty is not None and empty.bars == () and empty.actions_complete


@pytest.mark.parametrize("changes", [
    {"start": date(2023, 1, 3), "actions_start": date(2023, 1, 3)},
    {"actions_start": date(2023, 1, 3)},
])
def test_provider_history_limits(instrument, records, changes):
    provider = RecordsProvider(instrument, records, ())
    with pytest.raises(ProviderError) as error:
        fetch(provider, **changes)
    assert error.value.code == "history_not_entitled"
    assert provider.calls == 0


def test_history_floor_is_inclusive(instrument):
    provider = RecordsProvider(instrument, (), ())
    result = fetch(provider, start=date(2023, 1, 4), end=date(2023, 1, 4), actions_start=date(2023, 1, 4))
    assert result is not None and result.bars == ()


def test_leap_year_entitlement_floor(instrument):
    provider = RecordsProvider(instrument, (), (), capabilities_override={"history_years": 1})
    result = fetch(provider, observed_at=datetime(2024, 2, 29, tzinfo=timezone.utc),
                   start=date(2023, 2, 28), end=date(2023, 2, 28), actions_start=date(2023, 2, 28))
    assert result is not None


@pytest.mark.parametrize("changes", [
    {"start": date(2025, 1, 4)}, {"actions_start": date(2025, 1, 3)},
    {"observed_at": datetime(2025, 1, 4)}, {"cache_not_before": datetime(2025, 1, 1)},
    {"availability_basis": "guessed"}, {"symbol": ""},
])
def test_invalid_request_rejected_before_fetch(instrument, records, changes):
    provider = RecordsProvider(instrument, records, ())
    with pytest.raises(ProviderError) as error:
        fetch(provider, **changes)
    assert error.value.code == "invalid_request"
    assert provider.calls == 0


def test_incomplete_batch_cannot_be_constructed(instrument, records):
    with pytest.raises(ProviderError) as error:
        ProviderBatch(instrument, records, (), ("a" * 64,), False)
    assert error.value.code == "incomplete_batch"


@pytest.mark.parametrize("mutation", ["bar_type", "action_type", "instrument_id", "currency", "adjustment", "mutable", "hash"])
def test_noncanonical_batch_rejected(instrument, records, mutation):
    bars, actions, hashes = records, (), ("a" * 64,)
    if mutation == "bar_type":
        bars = ({"massive_close": "100"},)
    elif mutation == "action_type":
        actions = ({"vendor_ratio": "2:1"},)
    elif mutation == "instrument_id":
        bars = (replace(records[0], instrument_id="other"),)
    elif mutation == "currency":
        bars = (replace(records[0], currency="EUR"),)
    elif mutation == "adjustment":
        bars = (replace(records[0], adjustment="split_adjusted"),)
    elif mutation == "mutable":
        bars = list(records)
    else:
        hashes = ("https://vendor/?apiKey=secret",)
    with pytest.raises(ProviderError) as error:
        ProviderBatch(instrument, bars, actions, hashes, True)
    assert error.value.code == "invalid_batch"


@pytest.mark.parametrize("outlier", [date(2025, 1, 1), date(2025, 1, 4)])
def test_out_of_range_response_rejected(instrument, outlier):
    provider = RecordsProvider(instrument, (daily_bar(session=outlier),), (), ignore_range=True)
    with pytest.raises(ProviderError) as error:
        fetch(provider)
    assert error.value.code == "invalid_batch"


def test_wrong_symbol_rejected(instrument, records):
    provider = RecordsProvider(replace(instrument, symbol="MSFT"), records, ())
    with pytest.raises(ProviderError) as error:
        fetch(provider)
    assert error.value.code == "invalid_batch"


def test_batch_is_frozen(instrument, records):
    result = fetch(RecordsProvider(instrument, records, ()))
    assert result is not None
    with pytest.raises(FrozenInstanceError):
        result.actions_complete = False
