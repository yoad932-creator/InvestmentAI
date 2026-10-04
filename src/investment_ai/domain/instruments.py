"""Stable instrument identity, independent of ticker changes and reuse."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid5

from investment_ai.domain.provenance import (
    Provenance, canonical_json, require, require_currency, require_date, require_text,
)
from investment_ai.errors import DataError


# Permanent project namespace. Changing it would change all derived instrument IDs.
INSTRUMENT_NAMESPACE = UUID("7f094a60-21df-4f42-b8a9-9019e8ef1195")


def stable_instrument_id(source_id: str, venue: str) -> str:
    """Derive identity from a stable source identifier (e.g. FIGI), never a ticker."""
    if not all(isinstance(item, str) and item.strip() and item == item.strip()
               for item in (source_id, venue)):
        raise DataError("identity_unresolved", "A stable source identifier and venue are required.")
    key = canonical_json({"source_id": source_id, "venue": venue})
    return str(uuid5(INSTRUMENT_NAMESPACE, key))


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    symbol: str
    exchange: str
    currency: str
    kind: str
    active: bool
    provenance: Provenance

    def __post_init__(self) -> None:
        for name in ("instrument_id", "symbol", "exchange", "kind"):
            require_text(getattr(self, name), name)
        require_currency(self.currency)
        require(type(self.active) is bool, "active")
        require(isinstance(self.provenance, Provenance), "provenance")


@dataclass(frozen=True)
class SymbolAlias:
    """A half-open [effective_from, effective_to) symbol interval."""

    instrument_id: str
    symbol: str
    effective_from: date
    effective_to: date | None

    def __post_init__(self) -> None:
        require_text(self.instrument_id, "instrument_id")
        require_text(self.symbol, "symbol")
        require_date(self.effective_from, "effective_from")
        if self.effective_to is not None:
            require_date(self.effective_to, "effective_to")
            require(self.effective_to > self.effective_from, "alias interval")
