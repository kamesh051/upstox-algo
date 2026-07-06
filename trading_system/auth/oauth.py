"""Upstox OAuth authorization-code flow.

Opens the login dialog in the browser, catches the redirect on a local HTTP
listener, exchanges the code for an access token, and persists it via
``TokenStore``. Re-run daily (tokens expire ~03:30 IST).
"""

from __future__ import annotations

import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from trading_system.auth.token_store import AuthError, TokenStore
from trading_system.logging_setup import get_logger

log = get_logger(__name__)

AUTH_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
LOGIN_TIMEOUT_SEC = 300


def build_login_url(api_key: str, redirect_uri: str) -> str:
    params = {
        "response_type": "code",
        "client_id": api_key,
        "redirect_uri": redirect_uri,
    }
    return f"{AUTH_DIALOG_URL}?{urlencode(params)}"


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures ?code=... from the OAuth redirect and stashes it on the server."""

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        self.server.auth_code = code  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            "<h2>Login successful — you can close this tab.</h2>"
            if code
            else "<h2>No authorization code received. Check the logs and retry.</h2>"
        )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args) -> None:  # silence stdlib access log
        pass


def _wait_for_code(redirect_uri: str, timeout_sec: int) -> str:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80

    server = HTTPServer((host, port), _CallbackHandler)
    server.auth_code = None  # type: ignore[attr-defined]
    server.timeout = timeout_sec

    done = threading.Event()

    def serve() -> None:
        # One request is all we need; handle_request honours server.timeout.
        server.handle_request()
        done.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    if not done.wait(timeout=timeout_sec):
        server.server_close()
        raise AuthError(f"Timed out waiting {timeout_sec}s for the OAuth redirect")
    server.server_close()

    code = server.auth_code  # type: ignore[attr-defined]
    if not code:
        raise AuthError("OAuth redirect arrived without a 'code' parameter")
    return code


def exchange_code_for_token(
    code: str, api_key: str, api_secret: str, redirect_uri: str
) -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    token = resp.json().get("access_token")
    if not token:
        raise AuthError(f"Token exchange response had no access_token: {resp.text}")
    return token


def run_login_flow(
    api_key: str,
    api_secret: str,
    redirect_uri: str,
    token_store: TokenStore,
    open_browser: bool = True,
) -> str:
    if not api_key or not api_secret:
        raise AuthError(
            "UPSTOX_API_KEY / UPSTOX_API_SECRET are not set. "
            "Create a .env file (see .env.example)."
        )
    login_url = build_login_url(api_key, redirect_uri)
    log.info("oauth.login_url", url=login_url)
    if open_browser:
        webbrowser.open(login_url)
    print(f"\nIf the browser didn't open, visit:\n{login_url}\n")

    code = _wait_for_code(redirect_uri, LOGIN_TIMEOUT_SEC)
    token = exchange_code_for_token(code, api_key, api_secret, redirect_uri)
    token_store.save(token)
    log.info("oauth.token_saved", path=str(token_store.path))
    return token
