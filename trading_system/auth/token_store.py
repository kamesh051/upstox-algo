"""Access-token persistence with Upstox's daily-expiry rule.

Upstox access tokens die at ~03:30 IST every day regardless of when they were
issued. ``TokenStore.get_valid_token()`` returns None once that boundary has
passed so callers re-login instead of hitting 401s mid-session.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
TOKEN_EXPIRY_TIME = time(3, 30)  # 03:30 IST daily


class AuthError(Exception):
    """Raised when the API rejects our token (401) or no valid token exists."""


def _expiry_after(issued_at: datetime) -> datetime:
    """First 03:30 IST strictly after ``issued_at``."""
    issued_ist = issued_at.astimezone(IST)
    candidate = issued_ist.replace(
        hour=TOKEN_EXPIRY_TIME.hour,
        minute=TOKEN_EXPIRY_TIME.minute,
        second=0,
        microsecond=0,
    )
    if issued_ist >= candidate:
        candidate += timedelta(days=1)
    return candidate


class TokenStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def save(self, access_token: str, issued_at: datetime | None = None) -> None:
        issued_at = issued_at or datetime.now(IST)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": access_token,
            "issued_at": issued_at.astimezone(IST).isoformat(),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_valid_token(self, now: datetime | None = None) -> str | None:
        """Return the stored token if it hasn't hit the 03:30 IST boundary, else None."""
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            issued_at = datetime.fromisoformat(payload["issued_at"])
            token = payload["access_token"]
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        now = now or datetime.now(IST)
        if now.astimezone(IST) >= _expiry_after(issued_at):
            return None
        return token or None

    def invalidate(self) -> None:
        self.path.unlink(missing_ok=True)
