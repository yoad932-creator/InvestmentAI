"""Provider-neutral daily acquisition contracts; no HTTP or execution logic."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Protocol

from investment_ai.domain.actions import CorporateAction, Dividend, Split
from investment_ai.domain.bars import DailyBar
from investment_ai.domain.instruments import Instrument
from investment_ai.errors import ProviderError


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ProviderError(code, message)


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    daily: bool
    raw_bars: bool
    splits: bool
    dividends: bool
    history_years: int

    def __post_init__(self) -> None:
        valid = (isinstance(self.provider, str) and bool(self.provider.strip())
                 and all(type(flag) is bool for flag in
                         (self.daily, self.raw_bars, self.splits, self.dividends))
                 and type(self.history_years) is int and self.history_years > 0)
        _require(valid, "invalid_capabilities", "Malformed provider capabilities.")


def require_daily_capabilities(capabilities: ProviderCapabilities) -> None:
    """Both action streams are required even when a date range has no events."""
    _require(isinstance(capabilities, ProviderCapabilities), "invalid_capabilities",
             "Expected canonical provider capabilities.")
    _require(all((capabilities.daily, capabilities.raw_bars, capabilities.splits,
                  capabilities.dividends)), "unsupported_capabilities",
             "Daily raw bars, splits, and dividends are required.")


@dataclass(frozen=True)
class ProviderBatch:
    """A completed acquisition, not a guarantee of continuous or valid market data.

    Empty bars/actions are valid. Endpoint failures are not empty results.
    Market-history quality (gaps, conflicting bars, OHLC relationships) is
    evaluated separately; this boundary verifies canonical types and identity.
    """

    instrument: Instrument
    bars: tuple[DailyBar, ...]
    actions: tuple[CorporateAction, ...]
    request_hashes: tuple[str, ...]
    actions_complete: bool

    def __post_init__(self) -> None:
        _require(self.actions_complete is True, "incomplete_batch",
                 "Both corporate-action streams must be complete.")
        valid = (isinstance(self.instrument, Instrument)
                 and type(self.bars) is tuple and type(self.actions) is tuple
                 and type(self.request_hashes) is tuple
                 and all(isinstance(bar, DailyBar) for bar in self.bars)
                 and all(isinstance(action, (Split, Dividend)) for action in self.actions)
                 and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
                         for item in self.request_hashes))
        _require(valid, "invalid_batch", "Expected immutable canonical batch records and hashes.")
        _require(all(bar.instrument_id == self.instrument.instrument_id
                     and bar.currency == self.instrument.currency and bar.adjustment == "raw"
                     for bar in self.bars), "invalid_batch",
                 "Bars must belong to the instrument and contain raw prices in its currency.")
        _require(all(action.instrument_id == self.instrument.instrument_id for action in self.actions),
                 "invalid_batch", "Actions must belong to the batch instrument.")


class MarketDataProvider(Protocol):
    """Replaceable adapter. Return a complete batch or raise ProviderError.

    Bar dates are inclusive [start, end]; action dates are inclusive
    [actions_start, end]. Complete pagination and both action streams before
    returning. Never substitute empty output for a failed or unauthorized stream.
    observed_at anchors the request, not first-seen observation timestamps.
    Actual retrieval availability remains in each record's Provenance.
    cache_not_before is a freshness floor for the adapter, not an as-of filter.
    """

    def capabilities(self) -> ProviderCapabilities: ...

    def fetch(self, symbol: str, start: date, end: date, observed_at: datetime,
              availability_basis: str, *, actions_start: date,
              cache_not_before: datetime) -> ProviderBatch: ...


def fetch_daily(provider: MarketDataProvider, symbol: str, start: date, end: date,
                observed_at: datetime, availability_basis: str, *, actions_start: date,
                cache_not_before: datetime) -> ProviderBatch:
    """Validate the common boundary without knowing an adapter's transport.

    History is measured in calendar years relative to observed_at's UTC date;
    a leap-day floor maps to February 28. Never clip a requested range.
    Readiness, calendar gaps, and quality checks remain separate concerns.
    """
    capabilities = provider.capabilities()
    require_daily_capabilities(capabilities)
    valid = (isinstance(symbol, str) and bool(symbol.strip()) and symbol == symbol.strip()
             and all(type(day) is date for day in (start, end, actions_start))
             and all(isinstance(instant, datetime) and instant.utcoffset() == timedelta(0)
                     for instant in (observed_at, cache_not_before))
             and availability_basis in ("reconstructed_history", "observed"))
    _require(valid, "invalid_request", "Expected a symbol, dates, UTC cutoffs, and availability basis.")
    _require(actions_start <= start <= end, "invalid_request", "Invalid inclusive request range.")
    anchor = observed_at.date()
    floor_year = anchor.year - capabilities.history_years
    if floor_year < 1:
        floor = date.min
    else:
        try:
            floor = anchor.replace(year=floor_year)
        except ValueError:
            floor = date(floor_year, 2, 28)
    _require(actions_start >= floor, "history_not_entitled",
             "Requested history exceeds the provider entitlement; range was not shortened.")
    batch = provider.fetch(symbol, start, end, observed_at, availability_basis,
                           actions_start=actions_start, cache_not_before=cache_not_before)
    _require(isinstance(batch, ProviderBatch), "invalid_batch", "Expected a canonical ProviderBatch.")
    _require(batch.instrument.symbol == symbol, "invalid_batch", "Provider returned a different symbol.")
    _require(all(start <= bar.session <= end for bar in batch.bars), "invalid_batch",
             "Provider returned a bar outside the inclusive requested range.")
    _require(all(actions_start <= (action.effective_date if isinstance(action, Split) else action.ex_date)
                 <= end for action in batch.actions), "invalid_batch",
             "Provider returned an action outside the inclusive requested range.")
    return batch
