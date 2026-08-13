"""쿼리 조각과 행 변환 (§2.4, 규칙 #6).

ORM 을 안 쓰면 `deleted == 0` 을 손으로 쓰고 싶어진다. FBA 가 정확히 그렇게 했다 —
**106곳에 하드코딩, 14곳 누락.** 하나만 빠져도 삭제된 데이터가 노출된다.
사람이 매번 기억해야 하는 규칙은 규칙이 아니다.

여기서는 조각을 **한 번만** 정의하고 레포지토리가 그것을 조립한다. 조건이 바뀌면
고칠 곳이 한 군데다. 레포지토리가 이 조각들을 쓰는지는
`tests/unit/test_architecture_rules.py` 가 검사한다.

전부 SQLAlchemy Core 표현식이다 — 방언별 SQL 은 컴파일 시점에 만들어진다 (§1.6).
이 파일에는 방언 이름이 한 번도 나오지 않아야 한다.
"""

from dataclasses import fields
from typing import Any

from sqlalchemy import Column, ColumnElement, Result, Select, Update, select, update

from app.common.db.model import Record, SoftDeletable


def columns(model: type[Record]) -> list[Column[Any]]:
    """dataclass 필드에 대응하는 테이블 컬럼을 순서대로 돌려준다.

    SELECT 목록을 손으로 적지 않는 이유: 모델에 필드를 추가하고 SELECT 를 고치는 것을
    잊으면 그 테이블을 읽는 **모든 요청**이 `TypeError` 로 죽는다. 여기서 뽑으면
    둘이 어긋날 수 없고, 테이블에 없는 필드는 `KeyError` 로 즉시 드러난다.
    """
    return [model.TABLE.c[field.name] for field in fields(model)]


def select_rows(model: type[Record]) -> Select[Any]:
    """`SELECT <모델 필드들> FROM <테이블>`. 필터는 붙지 않는다."""
    return select(*columns(model))


def alive(model: type[SoftDeletable]) -> ColumnElement[bool]:
    """살아 있는 행만 고르는 조건 (§1.4).

    빠뜨리면 탈퇴한 사용자가 목록에 다시 나타난다.
    """
    return model.TABLE.c.deleted == 0


def select_alive(model: type[SoftDeletable]) -> Select[Any]:
    """soft delete 대상 모델의 기본 조회. 레포지토리는 여기서 출발한다."""
    return select_rows(model).where(alive(model))


def soft_delete(model: type[SoftDeletable], *conditions: ColumnElement[bool]) -> Update:
    """`UPDATE t SET deleted = id WHERE ...` 를 만든다.

    `deleted = True` 가 아니라 자기 id 를 넣는 것이 §1.4 의 핵심이다. 서비스가
    이 값을 직접 계산하지 않게 여기서 한 번만 표현한다. 이미 삭제된 행을 다시
    건드리지 않도록 `alive()` 도 같이 붙인다 — 안 붙이면 두 번 호출했을 때
    `rowcount` 가 1 을 두 번 돌려주고, 서비스는 그걸 "지웠다"로 읽는다.

    `updated_at` 은 컬럼의 `onupdate` 가 채운다 (`schema.py`).
    """
    table = model.TABLE
    return update(table).where(*conditions, alive(model)).values(deleted=table.c.id)


def one_or_none[T: Record](model: type[T], result: Result[Any]) -> T | None:
    """결과 행 하나를 dataclass 로. 없으면 None."""
    row = result.mappings().one_or_none()
    return None if row is None else model(**row)


def all_of[T: Record](model: type[T], result: Result[Any]) -> list[T]:
    """결과 전부를 dataclass 목록으로."""
    return [model(**row) for row in result.mappings()]
