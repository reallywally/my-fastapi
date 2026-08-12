"""baseline — 빈 스키마

Revision ID: 0001_baseline
Revises:
Create Date: Phase 1

Phase 1 에는 모델이 아직 없다 (`common/db/` 의 믹스인과 첫 모델은 Phase 2).
이 리비전은 alembic 체인의 시작점만 만든다 — Phase 2 의 첫 autogenerate 가
이걸 부모로 잡고, 그때부터 `alembic check`(§2.3) 가 의미를 갖는다.

**빈 리비전이지 누락된 리비전이 아니다.** 앱은 `create_all()` 을 하지 않으므로
스키마는 여기서부터만 자란다.
"""

from collections.abc import Sequence

revision: str = '0001_baseline'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
