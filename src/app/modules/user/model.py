"""테이블 정의와 행의 모양. 업무 규칙은 `service.py` 에 있다 (§1.2).

두 가지가 한 파일에 있다:

- `user_table` — **스키마의 선언.** alembic autogenerate 가 보는 것 (§2.3)
- `User` — **행의 모양.** `SELECT` 가 읽어오고 서비스가 받는 것 (§1.6)

둘을 잇는 것은 `sql.columns()` 다. 어긋나면 그 자리에서 `KeyError` 로 죽는다.

`String` 에 항상 길이를 준다. MySQL 은 인덱스가 걸리는 문자열 컬럼에 길이가
필수라서, 길이 없는 `String` 하나가 나중에 방언 교체를 막는다 (§1.6).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Boolean, Column, Enum, String, Table, UniqueConstraint

from app.common.db import SoftDeletable, UTCDateTime, define_table


class UserStatus(StrEnum):
    active = 'active'
    #: 관리자가 잠근 상태. Phase 4 에서 잠금 즉시 토큰 무효화를 붙인다 (§6).
    locked = 'locked'


user_table: Table = define_table(
    'user',
    Column('username', String(50), nullable=False),
    Column('email', String(255), nullable=False),
    Column('nickname', String(50), nullable=False),
    # 평문은 어디에도 저장하지 않는다. 해싱은 `common/security/password.py`.
    Column('password_hash', String(255), nullable=False),
    # native_enum=False — 방언마다 enum 이 다르다. PostgreSQL 은 타입을 만들고 값을
    # 지우지 못하며, MySQL 은 ENUM 컬럼이고, SQLite 는 아예 없다. VARCHAR + CHECK 로
    # 통일하면 세 방언에서 같은 DDL 이 나온다 (§1.6).
    Column(
        'status',
        Enum(UserStatus, name='user_status', native_enum=False, length=20),
        default=UserStatus.active,
        nullable=False,
    ),
    Column('is_superuser', Boolean, default=False, nullable=False),
    Column('last_login_at', UTCDateTime, default=None, nullable=True),
    # §1.4 — `deleted` 를 unique 에 포함시켜야 탈퇴한 아이디를 재사용할 수 있다.
    # 살아 있는 행은 항상 deleted == 0 이므로 중복은 여전히 막힌다.
    UniqueConstraint('username', 'deleted'),
    UniqueConstraint('email', 'deleted'),
)


@dataclass(slots=True)
class User(SoftDeletable):
    TABLE: ClassVar[Table] = user_table

    username: str
    email: str
    nickname: str
    password_hash: str
    status: UserStatus
    is_superuser: bool
    last_login_at: datetime | None

    @property
    def is_locked(self) -> bool:
        return self.status is UserStatus.locked
