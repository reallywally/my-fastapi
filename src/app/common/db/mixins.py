"""모델 믹스인 (§1.4, Phase 2).

`Base` 에 넣지 않고 믹스인으로 두는 이유: 모든 테이블이 soft delete 대상은 아니다.
`SoftDeleteMixin` 을 상속했다는 사실 자체가 전역 필터(§2.4)의 적용 기준이 된다.

컬럼은 `declared_attr` 이 아니라 클래스 속성으로 둔다. `declared_attr` 은 서브클래스마다
정의가 달라져야 할 때(예: ForeignKey) 쓰는 것이고, 그냥 쓰면 믹스인의 속성이 매핑되지
않은 채로 남아서 `with_loader_criteria(SoftDeleteMixin, ...)` 가 경고를 뱉는다.
"""

from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.types import BigIntPK, UTCDateTime, utcnow


class PrimaryKeyMixin:
    """SQLite 에서 자동 증가하는 PK (`common/db/types.py` 참조)."""

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)


class DateTimeMixin:
    """생성·수정 시각. 항상 aware UTC 다."""

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SoftDeleteMixin:
    """삭제 표시에 **자기 행의 id** 를 넣는다 (§1.4).

    boolean 이면 `unique(slug)` 때문에 삭제된 값을 재사용할 수 없다. id 를 넣으면
    `unique(slug, deleted)` 가 성립해서 재등록이 가능하다 — 살아 있는 행은 항상
    `deleted == 0` 이므로 중복은 여전히 막힌다.

    쿼리에 `deleted == 0` 을 손으로 붙이지 마라. 전역 필터가 붙인다 (§2.4, 규칙 #6).
    """

    deleted: Mapped[int] = mapped_column(BigIntPK, default=0, server_default='0', nullable=False, index=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted != 0
