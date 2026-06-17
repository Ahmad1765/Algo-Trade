# file: src/api_server/auth.py
"""
Stateless auth for the dashboard.

Single shared password (Tier B) with a clean seam for a multi-user table later
(Tier C): swap the body of ``verify_credentials`` to a DB lookup and nothing
else changes. Sessions are stdlib HMAC-signed cookies — no new dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

COOKIE_NAME = "algo_session"
EXEMPT_PATHS = {"/health", "/login", "/logout"}
_DEFAULT_TTL = 7 * 24 * 3600  # 7 days


def _truthy(val: Optional[str]) -> bool:
    return str(val or "").strip().lower() in {"1", "true", "yes", "on"}


def auth_enabled() -> bool:
    """Auth is active whenever a dashboard password is configured."""
    return bool(os.getenv("DASHBOARD_PASSWORD", ""))


def assert_auth_config() -> None:
    """Fail fast so a public deploy can never launch unauthenticated."""
    password = os.getenv("DASHBOARD_PASSWORD", "")
    dev = _truthy(os.getenv("DEV_MODE"))
    if not password and not dev:
        raise RuntimeError(
            "DASHBOARD_PASSWORD is not set and DEV_MODE is off. Refusing to start "
            "an unauthenticated public server. Set DASHBOARD_PASSWORD (and "
            "SESSION_SECRET), or set DEV_MODE=1 for local development."
        )
    if password and not os.getenv("SESSION_SECRET", ""):
        raise RuntimeError("DASHBOARD_PASSWORD is set but SESSION_SECRET is missing.")


def verify_credentials(username: Optional[str], password: str) -> Optional[str]:
    """Return a subject (user id) on success, else None.

    Tier C seam: replace this body with a users-table lookup. Callers only
    rely on the (username, password) -> subject contract.
    """
    expected = os.getenv("DASHBOARD_PASSWORD", "")
    if expected and hmac.compare_digest(password or "", expected):
        return username or "admin"
    return None


def _secret() -> bytes:
    return os.getenv("SESSION_SECRET", "").encode()


def sign_session(subject: str, ttl: int = _DEFAULT_TTL) -> str:
    now = int(time.time())
    payload = {"sub": subject, "iat": now, "exp": now + ttl}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_session(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    raw, _, sig = token.rpartition(".")
    expected = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("sub")
