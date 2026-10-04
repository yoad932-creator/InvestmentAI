"""Task 1 fixtures use temporary paths and never need network access."""

from pathlib import Path

import pytest


@pytest.fixture
def config_path():
    return Path(__file__).resolve().parents[1] / "config" / "phase1.toml"


@pytest.fixture
def local_env(tmp_path):
    return {"LOCALAPPDATA": str(tmp_path / "Local")}


@pytest.fixture
def write_config(config_path, tmp_path):
    def write(old, new):
        original = config_path.read_text(encoding="utf-8")
        assert old in original, "Test input replacement no longer matches fixture"
        path = tmp_path / "phase1.toml"
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        return path

    return write
