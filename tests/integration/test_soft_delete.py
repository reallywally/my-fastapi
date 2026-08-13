"""soft delete 조각과 `SoftDeletable` (§1.4, §2.4).

FBA 는 `deleted = 0` 을 106곳에 손으로 붙였고 14곳을 빠뜨렸다. ORM 전역 필터가
없는 지금, 그 자리를 지키는 것은 **조각이 한 곳에만 있다는 사실**이다
(`common/db/sql.py`). 여기서 검증하는 것은 그 조각이 실제로 맞게 도는가다.

이 테스트 전용 테이블을 쓴다 — `METADATA` 를 오염시키면 autogenerate 가 이 테이블을
잡아버리므로 별도 `MetaData` 를 만든다.
"""

from dataclasses import dataclass
from typing import ClassVar

import pytest
import pytest_asyncio
from sqlalchemy import Column, MetaData, String, Table, UniqueConstraint, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import (
    NAMING_CONVENTION,
    SoftDeletable,
    all_of,
    deleted_column,
    id_column,
    one_or_none,
    select_alive,
    select_rows,
    soft_delete,
    timestamp_columns,
)

pytestmark = pytest.mark.asyncio(loop_scope='session')

_metadata = MetaData(naming_convention=NAMING_CONVENTION)

widget_table = Table(
    'probe_widget',
    _metadata,
    id_column(),
    *timestamp_columns(),
    deleted_column(),
    Column('slug', String(50), nullable=False),
    # §1.4 — boolean 이면 삭제된 slug 를 재사용할 수 없다. id 를 넣으면 가능해진다.
    UniqueConstraint('slug', 'deleted'),
)


@dataclass(slots=True)
class Widget(SoftDeletable):
    TABLE: ClassVar[Table] = widget_table

    slug: str


async def _insert(db: AsyncConnection, slug: str) -> Widget:
    result = await db.execute(insert(widget_table).values(slug=slug))
    row = await db.execute(select_rows(Widget).where(widget_table.c.id == result.inserted_primary_key[0]))
    widget = one_or_none(Widget, row)
    assert widget is not None
    return widget


async def _slugs(db: AsyncConnection, *, include_deleted: bool = False) -> list[str]:
    stmt = select_rows(Widget) if include_deleted else select_alive(Widget)
    result = await db.execute(stmt.order_by(widget_table.c.slug))
    return [widget.slug for widget in all_of(Widget, result)]


@pytest_asyncio.fixture(loop_scope='session')
async def widgets(db: AsyncConnection) -> list[Widget]:
    await db.run_sync(_metadata.create_all)
    return [await _insert(db, slug) for slug in ('alive-1', 'alive-2', 'doomed')]


async def test_alive_hides_deleted_rows(widgets, db: AsyncConnection):
    await db.execute(soft_delete(Widget, widget_table.c.slug == 'doomed'))

    assert await _slugs(db) == ['alive-1', 'alive-2']
    assert await _slugs(db, include_deleted=True) == ['alive-1', 'alive-2', 'doomed']


async def test_soft_delete_stores_the_row_id_not_a_boolean(widgets, db: AsyncConnection):
    """§1.4 의 핵심. 1 이나 True 가 아니라 자기 id 다."""
    target = widgets[2]
    await db.execute(soft_delete(Widget, widget_table.c.id == target.id))

    result = await db.execute(select_rows(Widget).where(widget_table.c.id == target.id))
    stored = one_or_none(Widget, result)

    assert stored is not None
    assert stored.deleted == target.id
    assert stored.is_deleted is True


async def test_deleting_twice_does_not_report_a_second_deletion(widgets, db: AsyncConnection):
    """`soft_delete` 가 `alive()` 를 같이 붙이는 이유. 안 붙이면 rowcount 가 거짓말을 한다."""
    statement = soft_delete(Widget, widget_table.c.slug == 'doomed')

    assert (await db.execute(statement)).rowcount == 1
    assert (await db.execute(statement)).rowcount == 0


async def test_soft_delete_touches_updated_at(widgets, db: AsyncConnection):
    """`onupdate` 가 붙어 있어서 SET 절에 손으로 넣지 않아도 갱신된다 (`common/db/schema.py`)."""
    target = widgets[2]
    await db.execute(soft_delete(Widget, widget_table.c.id == target.id))

    result = await db.execute(select_rows(Widget).where(widget_table.c.id == target.id))
    stored = one_or_none(Widget, result)

    assert stored is not None
    assert stored.updated_at >= target.updated_at


async def test_slug_can_be_reused_after_deletion(widgets, db: AsyncConnection):
    """이게 `deleted = id` 를 쓰는 이유 전부다."""
    await db.execute(soft_delete(Widget, widget_table.c.slug == 'doomed'))
    await _insert(db, 'doomed')

    assert await _slugs(db) == ['alive-1', 'alive-2', 'doomed']


async def test_duplicate_slug_among_live_rows_is_still_rejected(widgets, db: AsyncConnection):
    """재사용을 허용하면서 중복은 막아야 한다 — 둘 다 성립해야 의미가 있다."""
    with pytest.raises(IntegrityError):
        await _insert(db, 'alive-1')


async def test_timestamps_are_timezone_aware(widgets):
    """tz 를 버리는 방언에서도 aware UTC 로 돌아온다 (`common/db/types.py`)."""
    row = widgets[0]

    assert row.created_at.tzinfo is not None
    assert row.updated_at.tzinfo is not None


async def test_primary_key_autoincrements(widgets):
    """`BIGINT PRIMARY KEY` 는 SQLite 에서 rowid 별칭이 아니라 자동 증가하지 않는다.

    `BigIntPK` 가 sqlite variant 로 INTEGER 를 렌더링해서 이걸 막는다.
    """
    assert all(widget.id > 0 for widget in widgets)
    assert len({widget.id for widget in widgets}) == len(widgets)
