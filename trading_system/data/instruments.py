"""Upstox instruments master: download, cache, and symbol → instrument_key lookup.

The master is a public gzipped JSON dump per exchange, no auth needed:
https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import httpx

from trading_system.logging_setup import get_logger

log = get_logger(__name__)

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


class InstrumentLookupError(Exception):
    pass


class InstrumentStore:
    """Caches the NSE instruments master on disk and resolves equity symbols."""

    def __init__(self, cache_dir: Path | str, max_age_days: int = 7):
        self.cache_dir = Path(cache_dir)
        self.max_age_days = max_age_days
        self.cache_file = self.cache_dir / "instruments_nse.json.gz"
        self._lookup: dict[str, dict] | None = None

    # -- download / cache ---------------------------------------------------

    def _cache_is_fresh(self) -> bool:
        if not self.cache_file.exists():
            return False
        age_days = (time.time() - self.cache_file.stat().st_mtime) / 86400
        return age_days < self.max_age_days

    def refresh(self, force: bool = False) -> None:
        if self._cache_is_fresh() and not force:
            log.debug("instruments.cache_fresh", path=str(self.cache_file))
            return
        log.info("instruments.downloading", url=INSTRUMENTS_URL)
        resp = httpx.get(INSTRUMENTS_URL, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_bytes(resp.content)
        self._lookup = None  # force re-parse
        log.info(
            "instruments.downloaded",
            path=str(self.cache_file),
            bytes=len(resp.content),
        )

    # -- lookup ---------------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        if self._lookup is not None:
            return self._lookup
        if not self.cache_file.exists():
            self.refresh()
        with gzip.open(self.cache_file, "rt", encoding="utf-8") as f:
            records = json.load(f)
        lookup: dict[str, dict] = {}
        for rec in records:
            # NSE equities only; the master also carries indices, ETFs, F&O etc.
            if rec.get("segment") == "NSE_EQ" and rec.get("instrument_type") == "EQ":
                lookup[rec["trading_symbol"].upper()] = rec
        self._lookup = lookup
        log.info("instruments.loaded", nse_eq_count=len(lookup))
        return lookup

    def instrument_key(self, symbol: str) -> str:
        """Resolve an NSE trading symbol (e.g. RELIANCE) to its instrument_key."""
        rec = self._load().get(symbol.upper())
        if rec is None:
            raise InstrumentLookupError(
                f"Symbol {symbol!r} not found in NSE_EQ instruments master"
            )
        return rec["instrument_key"]

    def resolve_many(self, symbols: list[str]) -> dict[str, str]:
        return {s: self.instrument_key(s) for s in symbols}
