"""Canonical daily OHLCV records; no calendar or indicator calculations."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

from investment_ai.domain.provenance import (
    Provenance, require, require_currency, require_date, require_decimal, require_text, require_utc,
)


@dataclass(frozen=True)
class DailyBar:
    instrument_id: str
    session: date
    source_start: datetime
    source_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    currency: str
    adjustment: str
    coverage_profile: str
    provenance: Provenance
    timeframe: ClassVar[str] = "1d"

    def __post_init__(self) -> None:
        require_text(self.instrument_id, "instrument_id")
        require_date(self.session, "session")
        require_utc(self.source_start, "source_start")
        require_utc(self.source_end, "source_end")
        require(self.source_end > self.source_start, "bar interval")
        for name in ("open", "high", "low", "close", "volume"):
            require_decimal(getattr(self, name), name)
        require_currency(self.currency)
        require(self.adjustment in ("raw", "split_adjusted"), "adjustment")
        require_text(self.coverage_profile, "coverage_profile")
        require(isinstance(self.provenance, Provenance), "provenance")
