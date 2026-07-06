"""Configuration layer.

Two sources, kept deliberately separate:

- ``Secrets``  — credentials from environment variables / ``.env`` only.
- ``AppConfig`` — everything else, from a YAML file (checked into the repo).

Money convention: all absolute money amounts in config and throughout the
system are **paise (int)**. Percentages are floats (e.g. ``0.10`` = 10%).
Market-data prices (candle OHLC) are the one exception — they stay float
because indicator math needs them that way; they are converted to paise at
the accounting boundary (order values, P&L).
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "trading_system" / "config" / "config.yaml"


class Secrets(BaseSettings):
    """Credentials. Environment variables / .env only — never YAML, never code."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = "http://localhost:8721/callback"
    # Escape hatch: paste a token obtained elsewhere instead of running the OAuth flow.
    upstox_access_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    confirm_live_trading: str = ""

    def live_trading_confirmed(self) -> bool:
        return self.confirm_live_trading.lower() == "yes"


class InstrumentConfig(BaseModel):
    symbol: str  # NSE trading symbol, e.g. "RELIANCE"
    name: str = ""  # company name, used later for news queries


class GatesConfig(BaseModel):
    """Trade-frequency gates (Phase 2.5 #1). Cut cost drag by trading less."""

    enabled: bool = True
    min_confirmations: int = Field(default=2, ge=0, le=3)
    volume_surge_mult: float = Field(default=1.5, gt=0)
    cooldown_candles: int = Field(default=8, ge=0)  # per symbol, after any exit
    max_trades_per_symbol_per_day: int = Field(default=2, ge=1)
    max_trades_per_day: int = Field(default=6, ge=1)


class RiskConfig(BaseModel):
    capital_paise: int = Field(gt=0, description="Total trading capital in paise")
    max_capital_per_trade_pct: float = Field(default=0.10, gt=0, le=1)
    max_concurrent_positions: int = Field(default=3, ge=1)
    daily_max_loss_pct: float = Field(default=0.02, gt=0, le=1)
    mandatory_stop_loss: bool = True
    gates: GatesConfig = GatesConfig()


class DataConfig(BaseModel):
    cache_dir: Path = Path("data_cache")
    db_file: str = "market_data.sqlite"
    instruments_max_age_days: int = 7
    # REST API base; v3 needed for 15-minute historical candles.
    api_base: str = "https://api.upstox.com"
    rate_limit_per_sec: float = 20.0
    rate_limit_burst: int = 20

    @property
    def db_path(self) -> Path:
        return self.cache_dir / self.db_file


class FeedConfig(BaseModel):
    mode: Literal["ltpc", "full"] = "ltpc"  # ltpc carries all a candle builder needs
    stale_after_seconds: float = Field(default=30.0, gt=0)
    reconnect_initial_delay: float = Field(default=1.0, gt=0)
    reconnect_max_delay: float = Field(default=60.0, gt=0)


class PaperConfig(BaseModel):
    strategy: str = "supertrend_follow"  # CLI --strategy overrides
    interval: str = "15minute"  # signal timeframe; match what was backtested
    slippage_pct: float = Field(default=0.0003, ge=0)
    heartbeat_minutes: float = Field(default=60, gt=0)


class UiConfig(BaseModel):
    enabled: bool = False  # or `paper --dashboard`
    host: str = "127.0.0.1"  # localhost only; controls (session 7) require this
    port: int = 8765
    event_queue_size: int = Field(default=2000, ge=16)


class BacktestConfig(BaseModel):
    slippage_pct: float = Field(default=0.0003, ge=0)  # 0.03% per side
    square_off_time: time = time(15, 15)  # candle-start time of forced exit
    no_new_entries_after: time = time(14, 30)
    reports_dir: Path = Path("reports")
    walk_forward_train_days: int = Field(default=120, ge=1)
    walk_forward_validate_days: int = Field(default=30, ge=1)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_output: bool = True


class AppConfig(BaseModel):
    mode: Literal["backtest", "paper", "live"] = "backtest"
    instruments: list[InstrumentConfig]
    risk: RiskConfig
    data: DataConfig = DataConfig()
    feed: FeedConfig = FeedConfig()
    paper: PaperConfig = PaperConfig()
    ui: UiConfig = UiConfig()
    backtest: BacktestConfig = BacktestConfig()
    logging: LoggingConfig = LoggingConfig()

    @field_validator("instruments")
    @classmethod
    def _non_empty(cls, v: list[InstrumentConfig]) -> list[InstrumentConfig]:
        if not v:
            raise ValueError("instrument list must not be empty")
        return v

    @property
    def symbols(self) -> list[str]:
        return [i.symbol for i in self.instruments]


def load_config(path: Path | str | None = None) -> AppConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(cfg_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)
