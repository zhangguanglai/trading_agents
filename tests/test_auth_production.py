import logging

import pytest
from fastapi import HTTPException

from api.services import auth_service


def test_production_requires_unique_secret_and_allowed_email(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("TA_APP_SECRET_KEY", raising=False)
    monkeypatch.delenv("TA_ALLOWED_EMAILS", raising=False)

    with pytest.raises(RuntimeError, match="TA_APP_SECRET_KEY"):
        auth_service.validate_production_config()

    monkeypatch.setenv("TA_APP_SECRET_KEY", "a-unique-production-secret")
    with pytest.raises(RuntimeError, match="TA_ALLOWED_EMAILS"):
        auth_service.validate_production_config()

    monkeypatch.setenv("TA_ALLOWED_EMAILS", "User@Example.com")
    auth_service.validate_production_config()


def test_production_email_allowlist_fails_closed_and_ignores_case(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.delenv("TA_ALLOWED_EMAILS", raising=False)
    assert not auth_service.is_email_allowed("user@example.com")

    monkeypatch.setenv("TA_ALLOWED_EMAILS", "User@Example.com, second@example.com")
    assert auth_service.is_email_allowed("user@example.com")
    assert auth_service.is_email_allowed("SECOND@example.com")
    assert not auth_service.is_email_allowed("other@example.com")


def test_development_empty_allowlist_preserves_existing_behavior(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("TA_ALLOWED_EMAILS", raising=False)
    assert auth_service.is_email_allowed("anyone@example.com")


def test_production_without_smtp_neither_returns_nor_logs_code(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "production")
    for key in ("MAIL_HOST", "MAIL_SERVER", "SMTP_HOST"):
        monkeypatch.delenv(key, raising=False)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError, match="SMTP"):
        auth_service.send_login_code("user@example.com", "123456")

    assert "123456" not in caplog.text


def test_request_code_returns_503_when_production_smtp_is_missing(monkeypatch):
    from api.main import AuthRequestCodeRequest
    from api.routers.auth import request_login_code

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TA_ALLOWED_EMAILS", "user@example.com")
    for key in ("MAIL_HOST", "MAIL_SERVER", "SMTP_HOST"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        request_login_code(AuthRequestCodeRequest(email="user@example.com"))
    assert exc_info.value.status_code == 503


def test_request_code_rejects_non_allowlisted_email_before_creating_code(monkeypatch):
    from api.main import AuthRequestCodeRequest
    from api.routers import auth as auth_router

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TA_ALLOWED_EMAILS", "allowed@example.com")
    monkeypatch.setattr(
        auth_router.auth_service,
        "upsert_login_code",
        lambda *_args: pytest.fail("a verification code must not be created"),
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_router.request_login_code(AuthRequestCodeRequest(email="blocked@example.com"))
    assert exc_info.value.status_code == 403
