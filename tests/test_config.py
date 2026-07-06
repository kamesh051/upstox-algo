from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_system.config import load_config
from trading_system.config.settings import AppConfig, DEFAULT_CONFIG_PATH


def test_default_config_loads():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.mode == "backtest"
    assert len(cfg.instruments) == 5
    assert "RELIANCE" in cfg.symbols
    assert cfg.risk.capital_paise == 50_000_000
    assert cfg.risk.max_concurrent_positions == 3


def test_capital_is_paise_int():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert isinstance(cfg.risk.capital_paise, int)


def test_empty_instruments_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"instruments": [], "risk": {"capital_paise": 1000}}
        )


def test_db_path_composed_from_cache_dir():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.data.db_path == Path("data_cache") / "market_data.sqlite"
