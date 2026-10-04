"""Synthetic canonical records for offline domain tests."""

from datetime import date, datetime, timezone
from decimal import Decimal

from investment_ai.domain.bars import DailyBar
from investment_ai.domain.provenance import Provenance


def provenance(**changes):
    values = dict(
        provider="fixture", payload_hash="a" * 64, source_revision="revision-1",
        available_at=datetime(2025, 1, 3, 13, tzinfo=timezone.utc),
        ingested_at=datetime(2025, 1, 4, 13, tzinfo=timezone.utc),
        availability_basis="reconstructed_history",
    )
    return Provenance(**(values | changes))


def daily_bar(**changes):
    values = dict(
        instrument_id="fixture-instrument-1", session=date(2025, 1, 2),
        source_start=datetime(2025, 1, 2, 5, tzinfo=timezone.utc),
        source_end=datetime(2025, 1, 3, 5, tzinfo=timezone.utc),
        open=Decimal("100.12345678901234567890123456789"), high=Decimal("102.50"),
        low=Decimal("99.25"), close=Decimal("101.125"), volume=Decimal("123456.75"),
        currency="USD", adjustment="raw", coverage_profile="massive_daily_vendor_v1",
        provenance=provenance(),
    )
    return DailyBar(**(values | changes))
