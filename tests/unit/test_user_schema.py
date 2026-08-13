"""사용자 스키마 (§1.2). 순수 검증 — DB 없이 밀리초 단위로 돈다."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.modules.user.model import User, UserStatus
from app.modules.user.schema import CreateUser, UpdateUser, UserOut


def _payload(**overrides) -> dict:
    return {
        'username': 'gildong',
        'email': 'gildong@example.com',
        'nickname': '홍길동',
        'password': 'hunter2-long-enough',
    } | overrides


def test_valid_payload():
    obj = CreateUser(**_payload())

    assert obj.username == 'gildong'
    assert obj.nickname == '홍길동'


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('username', 'ab'),  # 3자 미만
        ('username', 'x' * 51),
        ('username', 'has space'),
        ('username', 'has-dash'),  # 패턴은 영숫자와 _ 만
        ('username', '한글아이디'),
        ('email', 'not-an-email'),
        ('email', 'missing@tld'),
        ('nickname', ''),
        ('password', 'short'),
        ('password', 'x' * 129),  # argon2 는 입력 길이에 비례해 비싸다 — DoS 방어
    ],
)
def test_rejected_payloads(field: str, value: str):
    with pytest.raises(ValidationError):
        CreateUser(**_payload(**{field: value}))


def test_user_out_never_exposes_the_password_hash():
    """응답 스키마는 허용 목록이다. 모델 필드를 늘려도 새어나가지 않는다."""
    assert 'password_hash' not in UserOut.model_fields
    assert 'password' not in UserOut.model_fields


def test_user_out_reads_from_the_row_object():
    """행은 dataclass 다. DB 없이 만들 수 있다는 것이 ORM 을 걷어낸 이득 중 하나다."""
    now = datetime.now(UTC)
    user = User(
        id=7,
        created_at=now,
        updated_at=now,
        deleted=0,
        username='gildong',
        email='gildong@example.com',
        nickname='홍길동',
        password_hash='$argon2id$secret',
        status=UserStatus.active,
        is_superuser=False,
        last_login_at=None,
    )

    dumped = UserOut.model_validate(user).model_dump()

    assert dumped['id'] == 7
    assert '$argon2id$secret' not in str(dumped)


def test_update_only_reports_the_fields_that_were_sent():
    """부분 수정 — 생략한 필드를 None 으로 덮어쓰면 데이터가 지워진다."""
    assert UpdateUser(nickname='새이름').changes() == {'nickname': '새이름'}
    assert UpdateUser().changes() == {}


def test_update_ignores_explicit_nulls():
    assert UpdateUser(nickname=None, email=None).changes() == {}
