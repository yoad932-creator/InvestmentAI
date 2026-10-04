"""Behavioral checks for public policy, secret isolation, and stable identity."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path
import re

import pytest

from investment_ai.config import config_hash, load_api_key, load_settings
from investment_ai.errors import ConfigError


def test_phase1_defaults(config_path, local_env):
    settings = load_settings(config_path, local_env)
    assert settings is not None
    assert (settings.mode, settings.timeframe, settings.benchmark) == ("swing", "1d", "SPY")
    assert settings.horizons == (5, 10, 20)
    assert settings.primary_horizon == 10
    assert settings.bootstrap_history_days == 550
    assert len(settings.universe) == 20
    assert settings.liquidity.window == 20
    assert settings.liquidity.min_average_dollars == Decimal("20000000")
    assert settings.liquidity.min_average_shares is None
    assert settings.liquidity.daily_dollar_floor == Decimal("10000000")
    assert settings.liquidity.min_passing_fraction == 0.80
    assert settings.liquidity.max_day_fraction == 0.20
    assert settings.provider.history_years == 2
    assert settings.provider.request_interval_seconds == 13
    assert settings.provider.attempts == 4
    assert settings.provider.ready_hour_et == 8


@pytest.mark.parametrize("case", ["19", "51", "duplicate", "benchmark", "empty"])
def test_universe_bounds(case, config_path, tmp_path, local_env):
    text = config_path.read_text(encoding="utf-8")
    if case == "19":
        text = text.replace('"WMT", "HD",', '"WMT",')
    elif case == "51":
        text = text.replace('"WMT", "HD",', '"WMT", "HD",' + ','.join(f'"S{i}"' for i in range(31)))
    else:
        replacement = {"duplicate": " aapl ", "benchmark": "spy", "empty": " "}[case]
        text = text.replace('"MSFT"', f'"{replacement}"')
    path = tmp_path / "bad.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(path, local_env)


def test_universe_normalizes_symbols(write_config, local_env):
    settings = load_settings(write_config('"AAPL"', '" aapl "'), local_env)
    assert settings is not None
    assert settings.universe[0] == "AAPL"


@pytest.mark.parametrize(("old", "new"), [
    ('min_average_dollars = "20000000"', ''),
    ('min_average_dollars = "20000000"', 'min_average_dollars = "-1"'),
    ('min_average_dollars = "20000000"', 'min_average_dollars = "NaN"'),
    ('daily_dollar_floor = "10000000"', 'daily_dollar_floor = "0"'),
    ('min_passing_fraction = 0.80', 'min_passing_fraction = 1.1'),
    ('min_passing_fraction = 0.80', 'min_passing_fraction = -0.1'),
    ('max_day_fraction = 0.20', 'max_day_fraction = nan'),
    ('max_day_fraction = 0.20', 'max_day_fraction = -0.1'),
    ('max_day_fraction = 0.20', 'max_day_fraction = 1.1'),
    ('window = 20', 'window = 0'),
    ('version = "swing-daily-v1"', 'version = ""'),
    ('# Omit min_average_shares to disable the separate share-volume floor.', 'min_average_shares = "-1"'),
])
def test_missing_liquidity_policy_fails(old, new, write_config, local_env):
    with pytest.raises(ConfigError):
        load_settings(write_config(old, new), local_env)


def test_optional_share_volume_floor(write_config, local_env):
    settings = load_settings(write_config(
        '# Omit min_average_shares to disable the separate share-volume floor.',
        'min_average_shares = "250000"',
    ), local_env)
    assert settings is not None
    assert settings.liquidity.min_average_shares == Decimal("250000")


@pytest.mark.parametrize(("old", "new"), [
    ('mode = "swing"', 'mode = "short-term"'),
    ('timeframe = "1d"', 'timeframe = "1m"'),
    ('benchmark = "SPY"', 'benchmark = "QQQ"'),
    ('horizons = [5, 10, 20]', 'horizons = [1, 2, 3]'),
    ('primary_horizon = 10', 'primary_horizon = 5'),
    ('bootstrap_history_days = 550', 'bootstrap_history_days = 0'),
    ('mode = "swing"', 'mode = "swing"\nmanual_liquidity_override = true'),
])
def test_scope_changing_settings_fail(old, new, write_config, local_env):
    with pytest.raises(ConfigError):
        load_settings(write_config(old, new), local_env)


@pytest.mark.parametrize(("old", "new"), [
    ('history_years = 2', 'history_years = 0'),
    ('attempts = 4', 'attempts = true'),
    ('attempts = 4', 'attempts = 0'),
    ('ready_hour_et = 8', 'ready_hour_et = 24'),
    ('request_interval_seconds = 13.0', 'request_interval_seconds = -1'),
    ('base_url = "https://api.massive.com"', 'base_url = "http://api.massive.com"'),
    ('base_url = "https://api.massive.com"', 'base_url = "https://secret@api.massive.com"'),
])
def test_provider_policy_validation(old, new, write_config, local_env):
    with pytest.raises(ConfigError):
        load_settings(write_config(old, new), local_env)


def test_env_overrides_dotenv(tmp_path, config_path, local_env):
    dotenv = tmp_path / ".env"
    dotenv.write_text("MASSIVE_API_KEY=file-secret\n", encoding="utf-8")
    env = {**local_env, "MASSIVE_API_KEY": "env-secret"}
    assert load_api_key(env, dotenv) == "env-secret"
    settings = load_settings(config_path, env)
    assert settings is not None
    digest = config_hash(settings)
    assert isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
    assert "env-secret" not in repr(settings) + digest
    assert "file-secret" not in repr(settings) + digest


def test_dotenv_requires_explicit_opt_in(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("MASSIVE_API_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="API key"):
        load_api_key({}, None)
    assert load_api_key({}, dotenv) == "file-secret"


@pytest.mark.parametrize("key", ["", "   ", "unsafe\nheader"])
def test_invalid_api_key_rejected(key):
    with pytest.raises(ConfigError) as error:
        load_api_key({"MASSIVE_API_KEY": key}, None)
    assert error.value.code == "invalid_api_key"
    assert "unsafe" not in str(error.value)


def test_offline_needs_no_key(config_path, local_env):
    settings = load_settings(config_path, local_env)
    assert settings is not None
    assert settings.benchmark == "SPY"


def test_data_directory_default(config_path, local_env):
    settings = load_settings(config_path, local_env)
    assert settings is not None
    assert settings.data_dir == Path(local_env["LOCALAPPDATA"]) / "InvestmentAI"
    assert "OneDrive" not in str(settings.data_dir)


def test_hash_excludes_paths_and_includes_policy(config_path, local_env, tmp_path):
    settings = load_settings(config_path, local_env)
    assert settings is not None
    first = config_hash(settings)
    assert isinstance(first, str) and re.fullmatch(r"[0-9a-f]{64}", first)
    moved = replace(settings, data_dir=tmp_path / "elsewhere")
    assert config_hash(moved) == first
    changed = replace(settings, liquidity=replace(settings.liquidity, min_average_dollars=Decimal("30000000")))
    assert config_hash(changed) != first


def test_settings_are_frozen(config_path, local_env):
    settings = load_settings(config_path, local_env)
    assert settings is not None
    with pytest.raises(FrozenInstanceError):
        settings.mode = "short-term"
    with pytest.raises(FrozenInstanceError):
        settings.liquidity.window = 1


def test_config_errors_are_typed_and_do_not_echo_secrets(tmp_path, local_env):
    path = tmp_path / "bad.toml"
    path.write_text('MASSIVE_API_KEY = "sentinel-secret"\n[broken', encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_settings(path, local_env)
    assert error.value.code == "invalid_config"
    assert "sentinel-secret" not in str(error.value)


def test_fractional_horizons_rejected(write_config, local_env):
    with pytest.raises(ConfigError):
        load_settings(write_config('horizons = [5, 10, 20]', 'horizons = [5.0, 10.0, 20.0]'), local_env)


def test_semantically_equal_numbers_have_same_hash(config_path, write_config, local_env):
    original = load_settings(config_path, local_env)
    equivalent = load_settings(write_config('request_interval_seconds = 13.0', 'request_interval_seconds = 13'), local_env)
    assert config_hash(original) == config_hash(equivalent)


def test_invalid_dotenv_encoding_has_typed_error(tmp_path):
    dotenv = tmp_path / '.env'
    dotenv.write_bytes(b'MASSIVE_API_KEY=sentinel\xff')
    with pytest.raises(ConfigError) as error:
        load_api_key({}, dotenv)
    assert error.value.code == 'invalid_api_key'
    assert 'sentinel' not in str(error.value)
