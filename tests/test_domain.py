"""Canonical market records preserve values, time semantics, and identity."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from uuid import UUID

import pytest

from investment_ai.domain.actions import Dividend, Split
from investment_ai.domain.instruments import Instrument, SymbolAlias, stable_instrument_id
from investment_ai.domain.provenance import canonical_hash, canonical_json
from investment_ai.errors import DataError
from support import daily_bar, provenance


def test_decimal_roundtrip():
    bar = daily_bar()
    assert bar is not None
    encoded = canonical_json(asdict(bar))
    assert isinstance(encoded, str)
    decoded = json.loads(encoded)
    assert decoded["open"] == "100.12345678901234567890123456789"
    assert Decimal(decoded["open"]) == bar.open
    assert decoded["volume"] == "123456.75"
    assert decoded["source_start"] == "2025-01-02T05:00:00Z"
    assert decoded["session"] == "2025-01-02"


@pytest.mark.parametrize("field", ["available_at", "ingested_at"])
@pytest.mark.parametrize("timestamp", [datetime(2025, 1, 3), datetime(2025, 1, 3, tzinfo=timezone(timedelta(hours=2)))])
def test_non_utc_or_nonfinite_rejected(field, timestamp):
    with pytest.raises(DataError):
        provenance(**{field: timestamp})


@pytest.mark.parametrize("field", ["source_start", "source_end"])
def test_bar_timestamp_requires_utc(field):
    with pytest.raises(DataError):
        daily_bar(**{field: datetime(2025, 1, 3)})


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), 1.25])
def test_bar_nonfinite_or_float_rejected(field, value):
    with pytest.raises(DataError):
        daily_bar(**{field: value})


def test_alias_does_not_change_identity():
    instrument_id = stable_instrument_id("FIGI-FIRST", "XNAS")
    assert isinstance(instrument_id, str) and UUID(instrument_id).version == 5
    original = SymbolAlias(instrument_id, "OLD", date(2020, 1, 1), date(2024, 1, 1))
    renamed = SymbolAlias(instrument_id, "NEW", date(2024, 1, 1), None)
    recycled = SymbolAlias(stable_instrument_id("FIGI-SECOND", "XNAS"), "OLD", date(2024, 1, 1), None)
    assert original.instrument_id == renamed.instrument_id
    assert original.instrument_id != recycled.instrument_id
    assert stable_instrument_id("FIGI-FIRST", "XNAS") == instrument_id
    assert stable_instrument_id("FIGI-FIRST", "XNYS") != instrument_id


@pytest.mark.parametrize(("source", "venue"), [("", "XNAS"), ("   ", "XNAS"), (None, "XNAS"), ("FIGI", "")])
def test_missing_identity_rejected(source, venue):
    with pytest.raises(DataError) as error:
        stable_instrument_id(source, venue)
    assert error.value.code == "identity_unresolved"


def test_action_dates_do_not_imply_known_at():
    evidence = provenance(available_at=datetime(2025, 2, 10, 13, tzinfo=timezone.utc))
    split = Split("fixture-instrument-1", date(2025, 1, 15), Decimal("1"), Decimal("2"), evidence)
    dividend = Dividend("fixture-instrument-1", date(2025, 1, 15), Decimal("0.25"), "USD",
                        date(2025, 1, 1), date(2025, 1, 16), date(2025, 2, 1), evidence)
    assert split is not None and dividend is not None and evidence is not None
    assert split.provenance.available_at == datetime(2025, 2, 10, 13, tzinfo=timezone.utc)
    assert dividend.provenance.available_at == split.provenance.available_at
    assert dividend.declaration_date == date(2025, 1, 1)
    assert evidence.ingested_at == datetime(2025, 1, 4, 13, tzinfo=timezone.utc)


def test_payload_hash_stable():
    expected_json = '{"price":"100.125","symbol":"AAPL"}'
    first = canonical_hash({"price": Decimal("100.125"), "symbol": "AAPL"})
    assert first == hashlib.sha256(expected_json.encode()).hexdigest()
    assert first == canonical_hash({"symbol": "AAPL", "price": Decimal("100.125")})
    assert first != canonical_hash({"symbol": "AAPL", "price": Decimal("100.126")})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")])
def test_hash_rejects_nonfinite_nested_values(value):
    with pytest.raises(DataError):
        canonical_hash({"observations": [{"close": value}]})


def test_hash_rejects_nonstring_keys():
    with pytest.raises(DataError):
        canonical_hash({1: "a", "1": "b"})


def test_domain_records_are_frozen():
    bar = daily_bar()
    assert bar is not None
    with pytest.raises(FrozenInstanceError):
        bar.close = Decimal("999")
    with pytest.raises(FrozenInstanceError):
        bar.provenance.source_revision = "revised"


@pytest.mark.parametrize(("field", "value"), [("instrument_id", ""), ("currency", ""),
    ("coverage_profile", ""), ("adjustment", "unknown"), ("session", datetime(2025, 1, 2))])
def test_bar_contract_rejects_missing_metadata(field, value):
    with pytest.raises(DataError):
        daily_bar(**{field: value})


def test_bar_end_must_follow_start():
    with pytest.raises(DataError):
        daily_bar(source_end=datetime(2025, 1, 2, 5, tzinfo=timezone.utc))


@pytest.mark.parametrize(("field", "value"), [("provider", ""), ("source_revision", ""),
    ("payload_hash", "not-a-hash"), ("availability_basis", "unknown")])
def test_provenance_metadata_validated(field, value):
    with pytest.raises(DataError):
        provenance(**{field: value})


def test_instrument_preserves_identity_and_metadata():
    item = Instrument("fixed-id", "SPY", "ARCX", "USD", "etf", True, provenance())
    assert item is not None
    assert (item.instrument_id, item.symbol, item.currency, item.kind) == ("fixed-id", "SPY", "USD", "etf")
    with pytest.raises(FrozenInstanceError):
        item.symbol = "CHANGED"
    with pytest.raises(DataError):
        replace(item, currency="")
    with pytest.raises(DataError):
        replace(item, active="yes")


def test_alias_rejects_invalid_interval():
    with pytest.raises(DataError):
        SymbolAlias("fixed-id", "OLD", date(2025, 2, 1), date(2025, 1, 1))


@pytest.mark.parametrize("ratio", [Decimal("0"), Decimal("-1"), Decimal("NaN"), None])
def test_split_rejects_invalid_ratio(ratio):
    with pytest.raises(DataError):
        Split("fixed-id", date(2025, 1, 1), Decimal("1"), ratio, provenance())


def test_dividend_preserves_missing_optional_dates():
    item = Dividend("fixed-id", date(2025, 1, 1), Decimal("0.123456789"), "USD", None, None, None, provenance())
    assert item is not None
    assert item.cash_amount == Decimal("0.123456789")
    assert item.pay_date is None
    with pytest.raises(DataError):
        replace(item, cash_amount=Decimal("NaN"))
