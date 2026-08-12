"""테이블 매핑. SQLAlchemy 만 안다 — 업무 규칙은 `service.py` 에 있다 (§1.2)."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, DateTimeMixin, PrimaryKeyMixin, SoftDeleteMixin, UTCDateTime


class UserStatus(StrEnum):
    active = 'active'
    #: 관리자가 잠근 상태. Phase 4 에서 잠금 즉시 토큰 무효화를 붙인다 (§6).
    locked = 'locked'


class User(Base, PrimaryKeyMixin, DateTimeMixin, SoftDeleteMixin):
    __tablename__ = 'user'

    username: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50))
    #: 평문은 어디에도 저장하지 않는다. 해싱은 `common/security/password.py`.
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name='user_status', native_enum=False, length=20),
        default=UserStatus.active,
    )
    is_superuser: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    # §1.4 — `deleted` 를 unique 에 포함시켜야 탈퇴한 아이디를 재사용할 수 있다.
    # 살아 있는 행은 항상 deleted == 0 이므로 중복은 여전히 막힌다.
    __table_args__ = (
        UniqueConstraint('username', 'deleted'),
        UniqueConstraint('email', 'deleted'),
    )

    @property
    def is_locked(self) -> bool:
        return self.status is UserStatus.locked
