"""Synthetic canonical records for offline domain tests."""

from datetime import date, datetime, timezone
from decimal import Decimal

from investment_ai.domain.bars import DailyBar
from investment_ai.domain.provenance import Provenance


class FrozenClock:
    """Injected UTC clock; never reads the computer's time or timezone."""

    def __init__(self, instant):
        self.instant = instant

    def now(self):
        return self.instant


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


class RecordsProvider:
    """In-memory canonical source, with deliberate failure modes for contract tests."""

    def __init__(self, instrument, bars, actions, *, failed_stream=None,
                 capabilities_override=None, ignore_range=False):
        self.instrument = instrument
        self.bars = bars
        self.actions = actions
        self.failed_stream = failed_stream
        self.overrides = capabilities_override or {}
        self.ignore_range = ignore_range
        self.calls = 0

    def capabilities(self):
        from investment_ai.providers.base import ProviderCapabilities
        values = dict(provider="records", daily=True, raw_bars=True, splits=True,
                      dividends=True, history_years=2)
        return ProviderCapabilities(**(values | self.overrides))

    def fetch(self, symbol, start, end, observed_at, availability_basis, *, actions_start, cache_not_before):
        from investment_ai.domain.actions import Split
        from investment_ai.errors import ProviderError
        from investment_ai.providers.base import ProviderBatch
        self.calls += 1
        if self.failed_stream:
            raise ProviderError("incomplete_batch", "An action stream failed.")
        bars = tuple(b for b in self.bars if self.ignore_range or start <= b.session <= end)
        actions = tuple(a for a in self.actions
                        if actions_start <= (a.effective_date if isinstance(a, Split) else a.ex_date) <= end)
        return ProviderBatch(self.instrument, bars, actions, ("a" * 64,), True)


class RowsProvider:
    """A different source layout; consumers still receive only canonical records."""

    def __init__(self, instrument, rows, actions):
        self.instrument, self.rows, self.actions = instrument, rows, actions

    def capabilities(self):
        from investment_ai.providers.base import ProviderCapabilities
        return ProviderCapabilities("rows", True, True, True, True, 2)

    def fetch(self, symbol, start, end, observed_at, availability_basis, *, actions_start, cache_not_before):
        from investment_ai.domain.actions import Split
        from investment_ai.providers.base import ProviderBatch
        bars = tuple(daily_bar(session=day, close=Decimal(close))
                     for day, close in self.rows if start <= day <= end)
        actions = tuple(a for a in self.actions
                        if actions_start <= (a.effective_date if isinstance(a, Split) else a.ex_date) <= end)
        return ProviderBatch(self.instrument, bars, actions, ("a" * 64,), True)
