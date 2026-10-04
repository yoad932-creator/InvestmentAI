"""Corporate-action facts; effective dates never replace source availability."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from investment_ai.domain.provenance import (
    Provenance, require, require_currency, require_date, require_decimal, require_text,
)


@dataclass(frozen=True)
class Split:
    instrument_id: str
    effective_date: date
    split_from: Decimal
    split_to: Decimal
    provenance: Provenance

    def __post_init__(self) -> None:
        require_text(self.instrument_id, "instrument_id")
        require_date(self.effective_date, "effective_date")
        for name in ("split_from", "split_to"):
            value = getattr(self, name)
            require_decimal(value, name)
            require(value > 0, name)
        require(isinstance(self.provenance, Provenance), "provenance")


@dataclass(frozen=True)
class Dividend:
    instrument_id: str
    ex_date: date
    cash_amount: Decimal
    currency: str
    declaration_date: date | None
    record_date: date | None
    pay_date: date | None
    provenance: Provenance

    def __post_init__(self) -> None:
        require_text(self.instrument_id, "instrument_id")
        require_date(self.ex_date, "ex_date")
        require_decimal(self.cash_amount, "cash_amount")
        require_currency(self.currency)
        for name in ("declaration_date", "record_date", "pay_date"):
            value = getattr(self, name)
            if value is not None:
                require_date(value, name)
        require(isinstance(self.provenance, Provenance), "provenance")


CorporateAction = Split | Dividend
