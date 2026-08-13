"""토큰 encode/decode **만** 한다 (§2.2 해결 2).

FBA 의 `common/security/jwt.py` 는 `User` 모델과 `crud` 를 import 했다. 인프라가
도메인을 알게 되면서 순환 참조가 생겼고, 그걸 피하려고 함수 본문 안 import 를
15곳에 흩뿌렸다.

**여기에는 도메인이 없다.** 이 모듈이 아는 것은 "문자열 subject" 뿐이다.
"토큰 → 사용자 로드"는 `modules/auth/deps.py` 의 일이다 (Phase 5).

`lint-imports` 의 `common-knows-no-domain` 계약이 이걸 기계로 막는다.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

import jwt

from app.common.errors.exceptions import UnauthorizedError
from app.core.config import Settings


class TokenType(StrEnum):
    access = 'access'
    refresh = 'refresh'


@dataclass(frozen=True, slots=True)
class TokenPayload:
    subject: str
    token_type: TokenType
    #: 토큰 고유 id. 세션 저장소가 개별 무효화의 키로 쓴다 (§2.5, Phase 5).
    jti: str
    expires_at: datetime
    issued_at: datetime


def _ttl(settings: Settings, token_type: TokenType) -> int:
    if token_type is TokenType.access:
        return settings.access_token_ttl_seconds
    return settings.refresh_token_ttl_seconds


def encode(
    settings: Settings,
    *,
    subject: str,
    token_type: TokenType = TokenType.access,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, TokenPayload]:
    """토큰 문자열과, 그 안에 무엇이 들었는지를 함께 돌려준다.

    `jti` 와 만료를 호출자가 다시 파싱하지 않아도 되도록 payload 를 같이 준다 —
    발급 직후 세션 저장소에 기록해야 하기 때문이다.
    """
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=_ttl(settings, token_type))
    jti = uuid4().hex

    claims: dict[str, Any] = {
        'sub': subject,
        'typ': token_type.value,
        'jti': jti,
        'iat': int(issued_at.timestamp()),
        'exp': int(expires_at.timestamp()),
        'iss': settings.jwt_issuer,
        **(extra_claims or {}),
    }
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)
    return token, TokenPayload(
        subject=subject,
        token_type=token_type,
        jti=jti,
        expires_at=expires_at,
        issued_at=issued_at,
    )


def decode(settings: Settings, token: str, *, expect: TokenType | None = None) -> TokenPayload:
    """검증하고 payload 를 돌려준다. 실패는 전부 `UnauthorizedError` 로 통일한다.

    실패 사유를 세분화해서 응답에 노출하지 않는다 — 만료인지 위조인지를 알려주면
    공격자에게 정보를 준다. 만료만 별도 코드로 구분한다(재로그인 유도가 필요해서).
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={'require': ['sub', 'typ', 'jti', 'iat', 'exp', 'iss']},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError(code='auth.token_expired') from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError(code='auth.token_invalid') from exc

    try:
        token_type = TokenType(claims['typ'])
    except ValueError as exc:
        raise UnauthorizedError(code='auth.token_invalid') from exc

    # access 토큰을 refresh 자리에 넣는 혼동을 막는다. TTL 이 달라 실제로 위험하다.
    if expect is not None and token_type is not expect:
        raise UnauthorizedError(code='auth.token_invalid')

    return TokenPayload(
        subject=str(claims['sub']),
        token_type=token_type,
        jti=str(claims['jti']),
        expires_at=datetime.fromtimestamp(claims['exp'], tz=UTC),
        issued_at=datetime.fromtimestamp(claims['iat'], tz=UTC),
    )
