"""Massive JSON stays here; consumers receive only canonical market records."""
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from zoneinfo import ZoneInfo

from investment_ai.domain.actions import CorporateAction, Dividend, Split
from investment_ai.domain.bars import DailyBar
from investment_ai.domain.instruments import Instrument, stable_instrument_id
from investment_ai.domain.provenance import Provenance
from investment_ai.errors import DataError, ProviderError
from investment_ai.market.calendar import SessionCalendar, calendar_name

NEW_YORK = ZoneInfo("America/New_York")


def _mapping(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (DataError, ValueError, TypeError, KeyError, InvalidOperation, OverflowError, AttributeError):
            raise ProviderError("invalid_response", "Malformed Massive market-data record.") from None
    return guarded


def _decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Invalid number")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite number")
    return result


def midnight(day):
    return datetime.combine(day, time(), NEW_YORK).astimezone(timezone.utc)


@_mapping
def map_instrument(payload: Mapping[str, object], provenance: Provenance) -> Instrument:
    if payload["locale"] != "us" or payload["market"] != "stocks":
        raise ValueError("Unsupported market")
    venue = payload["primary_exchange"]
    calendar_name(venue)
    kind = {"CS": "stock", "ETF": "etf"}[payload["type"]]
    return Instrument(stable_instrument_id(payload["composite_figi"], venue), payload["ticker"],
                      venue, payload["currency_name"].upper(), kind, payload["active"], provenance)


@_mapping
def map_bar(payload: Mapping[str, object], instrument: Instrument,
            calendar: SessionCalendar, provenance: Provenance) -> DailyBar:
    stamp = payload["t"]
    if type(stamp) is not int:
        raise ValueError("Timestamp must be integer milliseconds")
    start = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=stamp)
    day = start.astimezone(NEW_YORK).date()
    if start != midnight(day):
        raise ValueError("Unexpected daily boundary")
    ready = calendar.ready_at(day)
    available = max(ready, provenance.ingested_at) if provenance.availability_basis == "observed" else ready
    return DailyBar(instrument.instrument_id, day, start, midnight(day + timedelta(days=1)),
                    *(_decimal(payload[key]) for key in ("o", "h", "l", "c", "v")),
                    instrument.currency, "raw", "massive_daily_vendor_v1",
                    replace(provenance, available_at=available))


@_mapping
def map_action(payload: Mapping[str, object], kind: str, instrument: Instrument,
               provenance: Provenance) -> CorporateAction:
    if payload["ticker"] != instrument.symbol:
        raise ValueError("Wrong action symbol")
    if kind == "split":
        return Split(instrument.instrument_id, date.fromisoformat(payload["execution_date"]),
                     _decimal(payload["split_from"]), _decimal(payload["split_to"]), provenance)
    if kind != "dividend":
        raise ValueError("Unsupported action")
    dates = [date.fromisoformat(payload[key]) if payload.get(key) is not None else None
             for key in ("declaration_date", "record_date", "pay_date")]
    return Dividend(instrument.instrument_id, date.fromisoformat(payload["ex_dividend_date"]),
                    _decimal(payload["cash_amount"]), payload["currency"].upper(), *dates, provenance)
