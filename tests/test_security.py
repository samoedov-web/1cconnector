"""Тесты аутентификации панели: пароли, токены, RBAC (п. 9 ТЗ)."""

import pytest

from connector.models import Role
from connector.security import (
    TokenError,
    create_token,
    hash_password,
    parse_token,
    verify_password,
)

SECRET = "test-secret"


def test_password_hash_roundtrip():
    stored = hash_password("s3cret")
    assert verify_password("s3cret", stored)
    assert not verify_password("wrong", stored)


def test_password_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_malformed_stored_hash_rejected():
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "")


def test_token_roundtrip():
    token = create_token(42, Role.OPERATOR, SECRET, ttl_seconds=60)
    data = parse_token(token, SECRET)
    assert data.user_id == 42
    assert data.role == Role.OPERATOR


def test_expired_token_rejected():
    token = create_token(1, Role.ADMIN, SECRET, ttl_seconds=-1)
    with pytest.raises(TokenError, match="истёк"):
        parse_token(token, SECRET)


def test_tampered_token_rejected():
    token = create_token(1, Role.AUDITOR, SECRET, ttl_seconds=60)
    encoded, signature = token.rsplit(".", 1)
    # Подменяем полезную нагрузку, оставляя старую подпись.
    forged = create_token(1, Role.ADMIN, SECRET, ttl_seconds=60).rsplit(".", 1)[0]
    with pytest.raises(TokenError, match="подпись"):
        parse_token(f"{forged}.{signature}", SECRET)


def test_token_signed_with_other_secret_rejected():
    token = create_token(1, Role.ADMIN, "other-secret", ttl_seconds=60)
    with pytest.raises(TokenError):
        parse_token(token, SECRET)


def test_missing_secret_blocks_token_operations():
    with pytest.raises(TokenError, match="SECRET_KEY"):
        create_token(1, Role.ADMIN, "", ttl_seconds=60)
    with pytest.raises(TokenError):
        parse_token("whatever.sig", "")
