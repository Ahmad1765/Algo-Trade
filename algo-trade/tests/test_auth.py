# file: tests/test_auth.py
"""Unit tests for the stdlib auth helpers in src/api_server/auth.py."""
from __future__ import annotations

import pytest

import src.api_server.auth as auth


@pytest.fixture
def env(monkeypatch):
    """Helper to set/clear the auth env vars and reload the module's view."""
    def _set(**kw):
        for k in ("DASHBOARD_PASSWORD", "SESSION_SECRET", "DEV_MODE"):
            monkeypatch.delenv(k, raising=False)
        for k, v in kw.items():
            monkeypatch.setenv(k, v)
    return _set


class TestAuthEnabled:
    def test_disabled_when_no_password(self, env):
        env(DEV_MODE="1")
        assert auth.auth_enabled() is False

    def test_enabled_when_password_set(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="s")
        assert auth.auth_enabled() is True


class TestAssertConfig:
    def test_raises_when_no_password_and_no_dev(self, env):
        env()  # nothing set
        with pytest.raises(RuntimeError):
            auth.assert_auth_config()

    def test_ok_in_dev_mode(self, env):
        env(DEV_MODE="1")
        auth.assert_auth_config()  # must not raise

    def test_raises_when_password_but_no_secret(self, env):
        env(DASHBOARD_PASSWORD="hunter2")
        with pytest.raises(RuntimeError):
            auth.assert_auth_config()


class TestCredentials:
    def test_correct_password_returns_subject(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="s")
        assert auth.verify_credentials(None, "hunter2") == "admin"

    def test_wrong_password_returns_none(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="s")
        assert auth.verify_credentials(None, "nope") is None


class TestSession:
    def test_roundtrip(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin")
        assert auth.verify_session(token) == "admin"

    def test_tampered_token_rejected(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin")
        assert auth.verify_session(token[:-1] + ("0" if token[-1] != "0" else "1")) is None

    def test_expired_token_rejected(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin", ttl=-1)
        assert auth.verify_session(token) is None

    def test_wrong_secret_rejected(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin")
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="different")
        assert auth.verify_session(token) is None
