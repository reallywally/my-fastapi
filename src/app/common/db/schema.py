"""테이블을 정의하는 도구 (§1.4).

ORM 믹스인 대신 **컬럼 팩토리**다. `Base` 를 상속하는 대신 `define_table()` 을 부른다.
얻는 것은 같다 — 공통 컬럼이 한 곳에 있고, 새 테이블이 그걸 빠뜨릴 수 없다.

`soft_delete=True` 가 규약이다. 그 테이블에는 `deleted` 컬럼이 있고, 조회에는
`alive()` 가 붙어야 하며 (§2.4), unique 제약에는 `deleted` 가 들어가야 한다 (§1.4).
마지막 항목은 `tests/unit/test_model_registry.py` 가 기계로 검사한다.
"""

from typing import Any

from sqlalchemy import Column, Table

from app.common.db.base import METADATA
from app.common.db.types import BigIntPK, UTCDateTime, utcnow


def id_column() -> Column[int]:
    """방언마다 자동 증가 방식이 다르다 — `BigIntPK` 가 흡수한다 (`types.py`)."""
    return Column('id', BigIntPK, primary_key=True, autoincrement=True)


def timestamp_columns() -> tuple[Column[Any], ...]:
    """생성·수정 시각. 항상 aware UTC 다.

    `onupdate` 는 Core `update()` 에도 적용된다 — SET 절에 `updated_at` 을 손으로
    넣을 필요가 없고, 넣는 것을 잊어서 시각이 멈추는 일도 없다.
    """
    return (
        Column('created_at', UTCDateTime, default=utcnow, nullable=False),
        Column('updated_at', UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False),
    )


def deleted_column() -> Column[int]:
    """삭제 표시에 **자기 행의 id** 를 넣는다 (§1.4).

    boolean 이면 `unique(slug)` 때문에 삭제된 값을 재사용할 수 없다. id 를 넣으면
    `unique(slug, deleted)` 가 성립해서 재등록이 가능하다 — 살아 있는 행은 항상
    `deleted = 0` 이므로 중복은 여전히 막힌다.
    """
    return Column('deleted', BigIntPK, default=0, server_default='0', nullable=False, index=True)


def define_table(name: str, *columns: Column[Any], soft_delete: bool = True, **kwargs: Any) -> Table:
    """공통 컬럼을 붙여 `Table` 을 만들고 `METADATA` 에 등록한다.

    등록이 곧 alembic autogenerate 의 입력이다 (§2.3). 모델 모듈을 import 하지 않으면
    테이블이 등록되지 않고, autogenerate 는 에러가 아니라 **빈 리비전**을 만든다 —
    그래서 `bootstrap/models.py` 가 있고, `test_model_registry.py` 가 그걸 검사한다.
    """
    common: list[Column[Any]] = [id_column(), *timestamp_columns()]
    if soft_delete:
        common.append(deleted_column())
    return Table(name, METADATA, *common, *columns, **kwargs)
