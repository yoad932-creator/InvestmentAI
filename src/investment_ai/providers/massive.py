"""Daily Massive adapter: one completed canonical batch or a typed failure."""
from datetime import date, datetime, timedelta
import re

from investment_ai.domain.provenance import Provenance, canonical_hash
from investment_ai.errors import DataError, ProviderError
from investment_ai.market.calendar import SessionCalendar
from investment_ai.providers.base import ProviderBatch, ProviderCapabilities, fetch_daily
from investment_ai.providers.massive_mapping import map_action, map_bar, map_instrument, midnight
from investment_ai.providers.transport import JsonTransport


class _MassiveAcquisition:
    """Unvalidated operation, called only through MassiveProvider's contract gate."""

    def __init__(self, transport: JsonTransport, calendar: SessionCalendar):
        self.transport, self.calendar = transport, calendar

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities("massive", True, True, True, True, self.transport.policy.history_years)

    @staticmethod
    def _provenance(record, page, basis, event_date):
        try:
            digest = canonical_hash(record)
            available = midnight(event_date)
            if basis == "observed":
                available = max(available, page.fetched_at)
            return Provenance("massive", digest, digest, available, page.fetched_at, basis)
        except (DataError, ValueError, TypeError):
            raise ProviderError("invalid_response", "Malformed provenance input.") from None

    def fetch(self, symbol, start, end, observed_at, availability_basis, *, actions_start, cache_not_before):
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", symbol):
            raise ProviderError("invalid_request", "Unsupported symbol format.")
        hashes, bars, actions = [], [], []
        fresh = max(cache_not_before, self.transport.clock.now() - timedelta(hours=24))
        metadata = list(self.transport.pages(f"/v3/reference/tickers/{symbol}", {"date": str(end)}, fresh))
        if len(metadata) != 1 or not isinstance(metadata[0].payload["results"], dict):
            raise ProviderError("invalid_response", "Expected one instrument record.")
        page = metadata[0]
        record = page.payload["results"]
        instrument = map_instrument(record, self._provenance(record, page, availability_basis, end))
        if instrument.symbol != symbol:
            raise ProviderError("invalid_response", "Metadata symbol differs from request.")
        hashes.append(page.request_hash)
        path = f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
        for page in self.transport.pages(path, {"adjusted": "false", "sort": "asc", "limit": "50000"}, cache_not_before):
            if page.payload.get("adjusted") is not False or page.payload.get("ticker") != symbol:
                raise ProviderError("invalid_response", "Expected raw daily bars for the requested symbol.")
            records = self._records(page)
            if "resultsCount" in page.payload and page.payload["resultsCount"] != len(records):
                raise ProviderError("invalid_response", "Inconsistent bar count.")
            for record in records:
                bar = map_bar(record, instrument, self.calendar,
                              self._provenance(record, page, availability_basis, start))
                bars.append(bar)
            hashes.append(page.request_hash)
        for kind, field in (("split", "execution_date"), ("dividend", "ex_dividend_date")):
            params = {"ticker": symbol, field + ".gte": str(actions_start), field + ".lte": str(end),
                      "sort": field + ".asc", "limit": "5000"}
            for page in self.transport.pages(f"/stocks/v1/{kind}s", params, fresh):
                for record in self._records(page):
                    try:
                        event = date.fromisoformat(record[field])
                    except (KeyError, ValueError, TypeError):
                        raise ProviderError("invalid_response", "Malformed corporate-action date.") from None
                    actions.append(map_action(record, kind, instrument,
                        self._provenance(record, page, availability_basis, event)))
                hashes.append(page.request_hash)
        return ProviderBatch(instrument, tuple(bars), tuple(actions), tuple(hashes), True)

    @staticmethod
    def _records(page):
        records = page.payload["results"]
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise ProviderError("invalid_response", "Expected a list of market-data records.")
        return records


class MassiveProvider(_MassiveAcquisition):
    def fetch(self, symbol: str, start: date, end: date, observed_at: datetime,
              availability_basis: str, *, actions_start: date,
              cache_not_before: datetime) -> ProviderBatch:
        # super() exposes the acquisition operation without recursively calling this gate.
        return fetch_daily(super(), symbol, start, end, observed_at, availability_basis,
                           actions_start=actions_start, cache_not_before=cache_not_before)
