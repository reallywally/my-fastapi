"""soft delete 를 전역으로 처리한다 (§2.4, 규칙 #6).

FBA 는 `deleted == 0` 을 쿼리마다 손으로 붙였다 — **106곳에 하드코딩, 14곳 누락.**
하나만 빠져도 삭제된 데이터가 노출된다. 사람이 매번 기억해야 하는 규칙은 규칙이 아니다.

여기서는 ORM 이벤트가 붙인다. `SoftDeleteMixin` 을 상속한 모든 모델에, 관계 로딩까지.
삭제분을 봐야 하면 **명시적으로** 끈다:

    await db.execute(select(Post).execution_options(include_deleted=True))

이 모듈을 import 하는 것만으로 리스너가 등록된다 (`common/db/__init__.py` 가 한다).
연결을 여는 게 아니라 ORM 설정이므로 §2.1 의 "import 부작용" 과는 다른 종류다.
"""

from typing import Any

from sqlalchemy import Update, event, update
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.common.db.mixins import SoftDeleteMixin
from app.core.constants import INCLUDE_DELETED


@event.listens_for(Session, 'do_orm_execute')
def _apply_soft_delete_filter(state: ORMExecuteState) -> None:
    if (
        not state.is_select
        or state.is_column_load
        or state.is_relationship_load
        or state.execution_options.get(INCLUDE_DELETED, False)
    ):
        return

    state.statement = state.statement.options(
        with_loader_criteria(SoftDeleteMixin, lambda cls: cls.deleted == 0, include_aliases=True)
    )


def soft_delete(model: type[Any], *conditions: Any) -> Update:
    """`UPDATE t SET deleted = id WHERE ...` 를 만든다.

    `deleted = True` 가 아니라 자기 id 를 넣는 것이 §1.4 의 핵심이다. 서비스가
    이 값을 직접 계산하지 않게 여기서 한 번만 표현한다.
    """
    return update(model).where(*conditions).values(deleted=model.id)
