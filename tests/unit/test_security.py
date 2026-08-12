"""토큰과 비밀번호 (§2.2 해결 2). DB·Redis 없이 돈다 — 도메인을 모르는 모듈이니까."""

from datetime import UTC, datetime

import jwt
import pytest

from app.common.errors import UnauthorizedError
from app.common.security import hash_password, verify_and_upgrade, verify_password
from app.common.security.token import TokenType, decode, encode
from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret='unit-test-secret-at-least-32-bytes-long', access_token_ttl_seconds=60)


def test_round_trip(settings: Settings):
    token, issued = encode(settings, subject='42')
    decoded = decode(settings, token)

    assert decoded.subject == '42'
    assert decoded.token_type is TokenType.access
    assert decoded.jti == issued.jti
    assert decoded.expires_at > datetime.now(UTC)


def test_payload_is_returned_at_encode_time(settings: Settings):
    """발급 직후 세션 저장소에 기록하려면 jti 를 다시 파싱하지 않아야 한다 (§2.5)."""
    _, issued = encode(settings, subject='7', token_type=TokenType.refresh)

    assert issued.token_type is TokenType.refresh
    assert issued.expires_at > issued.issued_at


def test_refresh_token_is_rejected_where_access_is_expected(settings: Settings):
    """TTL 이 2주와 15분으로 다르다. 바꿔치기가 통하면 세션이 사실상 무한이 된다."""
    token, _ = encode(settings, subject='1', token_type=TokenType.refresh)

    with pytest.raises(UnauthorizedError) as caught:
        decode(settings, token, expect=TokenType.access)

    assert caught.value.code == 'auth.token_invalid'


def test_expired_token_has_its_own_code(settings: Settings):
    """만료만 구분한다 — 화면이 재로그인을 유도해야 하기 때문."""
    expired = Settings(jwt_secret='unit-test-secret-at-least-32-bytes-long', access_token_ttl_seconds=-10)
    token, _ = encode(expired, subject='1')

    with pytest.raises(UnauthorizedError) as caught:
        decode(settings, token)

    assert caught.value.code == 'auth.token_expired'


def test_token_signed_with_another_secret_is_rejected(settings: Settings):
    other = Settings(jwt_secret='someone-elses-secret-at-least-32-bytes')
    token, _ = encode(other, subject='1')

    with pytest.raises(UnauthorizedError) as caught:
        decode(settings, token)

    assert caught.value.code == 'auth.token_invalid'


def test_token_from_another_issuer_is_rejected(settings: Settings):
    other = Settings(jwt_secret='unit-test-secret-at-least-32-bytes-long', jwt_issuer='some-other-service')
    token, _ = encode(other, subject='1')

    with pytest.raises(UnauthorizedError):
        decode(settings, token)


def test_alg_none_is_rejected(settings: Settings):
    """서명 없는 토큰을 통과시키는 고전적인 구멍."""
    forged = jwt.encode(
        {'sub': '1', 'typ': 'access', 'jti': 'x', 'iat': 0, 'exp': 9999999999, 'iss': 'my-fastapi'},
        key='',
        algorithm='none',
    )

    with pytest.raises(UnauthorizedError):
        decode(settings, forged)


def test_token_missing_required_claims_is_rejected(settings: Settings):
    partial = jwt.encode({'sub': '1'}, 'unit-test-secret-at-least-32-bytes-long', algorithm='HS256')

    with pytest.raises(UnauthorizedError):
        decode(settings, partial)


def test_password_hashing_round_trip():
    digest = hash_password('hunter2')

    assert digest != 'hunter2'
    assert digest.startswith('$argon2id$')
    assert verify_password('hunter2', digest)
    assert not verify_password('hunter3', digest)


def test_hashes_are_salted():
    assert hash_password('same') != hash_password('same')


def test_verify_tolerates_a_corrupted_hash():
    """DB 값이 깨졌을 때 500 이 아니라 '인증 실패' 여야 한다."""
    assert verify_password('hunter2', 'not-a-hash') is False


def test_long_passwords_are_not_truncated():
    """bcrypt 는 72바이트에서 자른다. argon2 는 자르지 않는다."""
    base = 'a' * 100
    digest = hash_password(base + 'X')

    assert not verify_password(base + 'Y', digest)


def test_verify_and_upgrade_reports_no_update_for_a_fresh_hash():
    ok, updated = verify_and_upgrade('hunter2', hash_password('hunter2'))

    assert ok
    assert updated is None
