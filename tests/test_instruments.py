import gzip
import json

import httpx
import pytest
import respx

from trading_system.data.instruments import (
    INSTRUMENTS_URL,
    InstrumentLookupError,
    InstrumentStore,
)

MASTER = [
    {
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "trading_symbol": "RELIANCE",
        "instrument_key": "NSE_EQ|INE002A01018",
        "name": "RELIANCE INDUSTRIES LTD",
    },
    {
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "trading_symbol": "TCS",
        "instrument_key": "NSE_EQ|INE467B01029",
        "name": "TATA CONSULTANCY SERV LT",
    },
    # Non-equity rows must be ignored by the lookup
    {
        "segment": "NSE_INDEX",
        "instrument_type": "INDEX",
        "trading_symbol": "NIFTY 50",
        "instrument_key": "NSE_INDEX|Nifty 50",
        "name": "NIFTY 50",
    },
    {
        "segment": "NSE_EQ",
        "instrument_type": "ETF",
        "trading_symbol": "NIFTYBEES",
        "instrument_key": "NSE_EQ|INF204KB14I2",
        "name": "NIPPON ETF",
    },
]


def _gz(payload) -> bytes:
    return gzip.compress(json.dumps(payload).encode("utf-8"))


@respx.mock
def test_download_and_lookup(tmp_path):
    respx.get(INSTRUMENTS_URL).mock(
        return_value=httpx.Response(200, content=_gz(MASTER))
    )
    store = InstrumentStore(tmp_path)
    assert store.instrument_key("RELIANCE") == "NSE_EQ|INE002A01018"
    assert store.instrument_key("tcs") == "NSE_EQ|INE467B01029"  # case-insensitive


@respx.mock
def test_non_equity_rows_excluded(tmp_path):
    respx.get(INSTRUMENTS_URL).mock(
        return_value=httpx.Response(200, content=_gz(MASTER))
    )
    store = InstrumentStore(tmp_path)
    with pytest.raises(InstrumentLookupError):
        store.instrument_key("NIFTY 50")
    with pytest.raises(InstrumentLookupError):
        store.instrument_key("NIFTYBEES")


@respx.mock
def test_fresh_cache_skips_download(tmp_path):
    route = respx.get(INSTRUMENTS_URL).mock(
        return_value=httpx.Response(200, content=_gz(MASTER))
    )
    store = InstrumentStore(tmp_path, max_age_days=7)
    store.refresh()
    assert route.call_count == 1
    # Second refresh within max_age hits the disk cache, not the network
    store2 = InstrumentStore(tmp_path, max_age_days=7)
    store2.refresh()
    assert route.call_count == 1
    assert store2.instrument_key("RELIANCE") == "NSE_EQ|INE002A01018"


@respx.mock
def test_force_refresh_redownloads(tmp_path):
    route = respx.get(INSTRUMENTS_URL).mock(
        return_value=httpx.Response(200, content=_gz(MASTER))
    )
    store = InstrumentStore(tmp_path)
    store.refresh()
    store.refresh(force=True)
    assert route.call_count == 2


@respx.mock
def test_unknown_symbol_raises(tmp_path):
    respx.get(INSTRUMENTS_URL).mock(
        return_value=httpx.Response(200, content=_gz(MASTER))
    )
    store = InstrumentStore(tmp_path)
    with pytest.raises(InstrumentLookupError, match="WIPRO"):
        store.instrument_key("WIPRO")
