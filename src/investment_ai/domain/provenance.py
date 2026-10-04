"""Immutable source evidence and lossless canonical JSON for market records."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import math
import re

from investment_ai.errors import DataError


def require(condition: bool, field: str) -> None:
    if not condition:
        raise DataError("invalid_data", f"Invalid market-data field: {field}")


def require_text(value: object, field: str) -> None:
    require(isinstance(value, str) and bool(value.strip()) and value == value.strip(), field)


def require_utc(value: object, field: str) -> None:
    require(isinstance(value, datetime) and value.utcoffset() == timedelta(0), field)


def require_date(value: object, field: str) -> None:
    require(type(value) is date, field)


def require_decimal(value: object, field: str) -> None:
    require(isinstance(value, Decimal) and value.is_finite(), field)


def require_currency(value: object) -> None:
    require(isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) is not None, "currency")


@dataclass(frozen=True)
class Provenance:
    provider: str
    payload_hash: str
    source_revision: str
    available_at: datetime
    ingested_at: datetime
    availability_basis: str

    def __post_init__(self) -> None:
        require_text(self.provider, "provider")
        require_text(self.source_revision, "source_revision")
        require(isinstance(self.payload_hash, str) and
                re.fullmatch(r"[0-9a-f]{64}", self.payload_hash) is not None, "payload_hash")
        require_utc(self.available_at, "available_at")
        require_utc(self.ingested_at, "ingested_at")
        require(self.availability_basis in ("reconstructed_history", "observed"), "availability_basis")


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        require(all(isinstance(key, str) for key in value), "JSON object keys")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Decimal):
        require_decimal(value, "JSON decimal")
        # Never normalize through the active Decimal context: that can round digits.
        return format(value, "f")
    if isinstance(value, datetime):
        require_utc(value, "JSON timestamp")
        return value.isoformat().replace("+00:00", "Z")
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, float):
        require(math.isfinite(value), "JSON number")
        return value
    if value is None or type(value) in (str, int, bool):
        return value
    raise DataError("invalid_data", "Unsupported canonical JSON value.")


def canonical_json(value: Mapping[str, object]) -> str:
    """Serialize mappings (including asdict records) without Decimal precision loss."""
    require(isinstance(value, Mapping), "JSON root")
    return json.dumps(_canonical_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def canonical_hash(value: Mapping[str, object]) -> str:
    """SHA-256 of deterministic UTF-8 canonical JSON; metadata stays explicit."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
