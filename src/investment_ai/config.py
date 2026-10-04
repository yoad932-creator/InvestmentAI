"""Validated public research policy, isolated from API credentials."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from dotenv import dotenv_values

from investment_ai.errors import ConfigError


def _require(condition: bool, field: str) -> None:
    if not condition:
        raise ConfigError("invalid_config", f"Invalid configuration field: {field}")


def _integer(value: object, field: str, minimum: int = 1, maximum: int | None = None) -> None:
    _require(type(value) is int and value >= minimum and (maximum is None or value <= maximum), field)


def _fraction(value: object, field: str) -> None:
    _require(type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 1, field)


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ConfigError("invalid_config", f"Invalid configuration field: {field}") from None
    _require(result.is_finite() and result > 0, field)
    return result


@dataclass(frozen=True)
class LiquidityPolicy:
    window: int
    min_average_dollars: Decimal
    min_average_shares: Decimal | None
    daily_dollar_floor: Decimal
    min_passing_fraction: float
    max_day_fraction: float
    version: str

    def __post_init__(self) -> None:
        _integer(self.window, "liquidity.window")
        for name in ("min_average_dollars", "daily_dollar_floor"):
            _positive_decimal(getattr(self, name), f"liquidity.{name}")
        if self.min_average_shares is not None:
            _positive_decimal(self.min_average_shares, "liquidity.min_average_shares")
        _fraction(self.min_passing_fraction, "liquidity.min_passing_fraction")
        _fraction(self.max_day_fraction, "liquidity.max_day_fraction")
        _require(isinstance(self.version, str) and bool(self.version.strip()), "liquidity.version")


@dataclass(frozen=True)
class ProviderPolicy:
    base_url: str
    history_years: int
    request_interval_seconds: float
    attempts: int
    ready_hour_et: int

    def __post_init__(self) -> None:
        _require(isinstance(self.base_url, str), "provider.base_url")
        url = urlsplit(self.base_url)
        _require(url.scheme == "https" and bool(url.hostname) and not (
            url.username or url.password or url.query or url.fragment
        ), "provider.base_url")
        _integer(self.history_years, "provider.history_years")
        _integer(self.attempts, "provider.attempts")
        _integer(self.ready_hour_et, "provider.ready_hour_et", 0, 23)
        interval = self.request_interval_seconds
        _require(type(interval) in (float, int) and math.isfinite(interval) and interval > 0,
                 "provider.request_interval_seconds")


@dataclass(frozen=True)
class Settings:
    mode: str
    timeframe: str
    benchmark: str
    universe: tuple[str, ...]
    horizons: tuple[int, ...]
    primary_horizon: int
    data_dir: Path
    liquidity: LiquidityPolicy
    provider: ProviderPolicy
    bootstrap_history_days: int = 550

    def __post_init__(self) -> None:
        _require(self.mode == "swing", "mode")
        _require(self.timeframe == "1d", "timeframe")
        _require(self.benchmark == "SPY", "benchmark")
        _require(self.horizons == (5, 10, 20) and
                 all(type(horizon) is int for horizon in self.horizons), "horizons")
        _require(type(self.primary_horizon) is int and self.primary_horizon == 10, "primary_horizon")
        _integer(self.bootstrap_history_days, "bootstrap_history_days")
        _require(20 <= len(self.universe) <= 50 and len(set(self.universe)) == len(self.universe), "universe")
        _require(all(self.universe) and "SPY" not in self.universe, "universe")


def _table(value: object, required: set[str], optional: set[str] | None = None) -> dict:
    _require(isinstance(value, dict), "table")
    _require(required <= value.keys() and value.keys() <= required | (optional or set()), "table fields")
    return value


def load_settings(path: Path, env: Mapping[str, str]) -> Settings:
    """Read TOML without loading credentials or creating runtime directories."""
    try:
        with path.open("rb") as source:
            raw = tomllib.load(source)
        raw = _table(raw, {"mode", "timeframe", "benchmark", "universe", "horizons",
                           "primary_horizon", "liquidity", "provider"},
                     {"bootstrap_history_days", "data_dir"})
        liquid = _table(raw["liquidity"], {"window", "min_average_dollars", "daily_dollar_floor",
                                         "min_passing_fraction", "max_day_fraction", "version"},
                        {"min_average_shares"})
        liquidity = LiquidityPolicy(
            **{key: value for key, value in liquid.items()
               if key not in ("min_average_dollars", "daily_dollar_floor", "min_average_shares")},
            min_average_dollars=_positive_decimal(liquid["min_average_dollars"], "liquidity.min_average_dollars"),
            daily_dollar_floor=_positive_decimal(liquid["daily_dollar_floor"], "liquidity.daily_dollar_floor"),
            min_average_shares=_positive_decimal(liquid["min_average_shares"], "liquidity.min_average_shares")
            if "min_average_shares" in liquid else None,
        )
        provider = ProviderPolicy(**_table(raw["provider"], {
            "base_url", "history_years", "request_interval_seconds", "attempts", "ready_hour_et",
        }))
        _require(isinstance(raw["universe"], list) and
                 all(isinstance(symbol, str) for symbol in raw["universe"]), "universe")
        _require(isinstance(raw["horizons"], list), "horizons")
        local = Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        directory = Path(raw.get("data_dir", local / "InvestmentAI"))
        _require(directory.is_absolute(), "data_dir")
        return Settings(
            mode=raw["mode"], timeframe=raw["timeframe"], benchmark=raw["benchmark"],
            universe=tuple(symbol.strip().upper() for symbol in raw["universe"]),
            horizons=tuple(raw["horizons"]), primary_horizon=raw["primary_horizon"],
            data_dir=directory, liquidity=liquidity, provider=provider,
            bootstrap_history_days=raw.get("bootstrap_history_days", 550),
        )
    except ConfigError:
        raise
    except (OSError, ValueError, TypeError):
        raise ConfigError("invalid_config", "Cannot read valid configuration.") from None


def load_api_key(env: Mapping[str, str], dotenv_path: Path | None = None) -> str:
    """Use the supplied environment first; read only an explicitly supplied .env."""
    key = env.get("MASSIVE_API_KEY")
    if key is None and dotenv_path is not None:
        try:
            with dotenv_path.open(encoding="utf-8") as source:
                key = dotenv_values(stream=source, interpolate=False).get("MASSIVE_API_KEY")
        except (OSError, UnicodeError):
            raise ConfigError("invalid_api_key", "Cannot read API key file.") from None
    if not isinstance(key, str) or not key.strip() or any(ord(char) < 32 for char in key):
        raise ConfigError("invalid_api_key", "A valid market-data API key is required.")
    return key.strip()


def config_hash(settings: Settings) -> str:
    """Hash semantic policy only, independent of secrets and machine-local paths."""
    payload = asdict(settings)
    payload.pop("data_dir")
    payload["provider"]["request_interval_seconds"] = float(settings.provider.request_interval_seconds)
    for field in ("min_passing_fraction", "max_day_fraction"):
        payload["liquidity"][field] = float(getattr(settings.liquidity, field))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
                           default=lambda value: format(value.normalize(), "f"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
