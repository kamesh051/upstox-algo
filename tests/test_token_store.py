from datetime import datetime

from trading_system.auth.token_store import IST, TokenStore


def test_roundtrip(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    issued = datetime(2026, 7, 1, 9, 0, tzinfo=IST)
    store.save("tok123", issued_at=issued)
    # Same trading day, before next 03:30 IST boundary
    assert store.get_valid_token(now=datetime(2026, 7, 1, 15, 0, tzinfo=IST)) == "tok123"


def test_token_expires_at_0330_ist_next_day(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    store.save("tok123", issued_at=datetime(2026, 7, 1, 9, 0, tzinfo=IST))
    assert store.get_valid_token(now=datetime(2026, 7, 2, 3, 29, tzinfo=IST)) == "tok123"
    assert store.get_valid_token(now=datetime(2026, 7, 2, 3, 30, tzinfo=IST)) is None
    assert store.get_valid_token(now=datetime(2026, 7, 2, 10, 0, tzinfo=IST)) is None


def test_token_issued_after_0330_valid_until_next_day(tmp_path):
    # Issued at 04:00 IST → boundary is 03:30 the NEXT day, not the same morning.
    store = TokenStore(tmp_path / "token.json")
    store.save("tok123", issued_at=datetime(2026, 7, 1, 4, 0, tzinfo=IST))
    assert store.get_valid_token(now=datetime(2026, 7, 1, 23, 0, tzinfo=IST)) == "tok123"
    assert store.get_valid_token(now=datetime(2026, 7, 2, 4, 0, tzinfo=IST)) is None


def test_missing_and_corrupt_files(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    assert store.get_valid_token() is None
    store.path.write_text("not json", encoding="utf-8")
    assert store.get_valid_token() is None


def test_invalidate(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    store.save("tok123")
    store.invalidate()
    assert store.get_valid_token() is None
