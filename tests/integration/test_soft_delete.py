"""soft delete 전역 필터와 믹스인 (§1.4, §2.4).

FBA 는 이 조건을 106곳에 손으로 붙였고 14곳을 빠뜨렸다. 여기서 검증하는 것은
**아무도 조건을 쓰지 않았는데도 삭제분이 안 보이는가** 다.

Phase 3 의 첫 모델이 나오기 전이라 이 테스트 전용 모델을 쓴다 — `Base.metadata`
를 오염시키면 autogenerate 가 이 테이블을 잡아버리므로 별도 registry 를 만든다.
"""

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.db.base import NAMING_CONVENTION
from app.common.db.mixins import DateTimeMixin, PrimaryKeyMixin, SoftDeleteMixin
from app.common.db.soft_delete import soft_delete

pytestmark = pytest.mark.asyncio(loop_scope='session')


class ProbeBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Widget(ProbeBase, PrimaryKeyMixin, DateTimeMixin, SoftDeleteMixin):
    __tablename__ = 'probe_widget'

    slug: Mapped[str] = mapped_column(String(50))

    # §1.4 — boolean 이면 삭제된 slug 를 재사용할 수 없다. id 를 넣으면 가능해진다.
    __table_args__ = (UniqueConstraint('slug', 'deleted'),)


@pytest_asyncio.fixture(loop_scope='session')
async def widgets(db_connection: AsyncConnection, db: AsyncSession) -> list[Widget]:
    await db_connection.run_sync(ProbeBase.metadata.create_all)
    rows = [Widget(slug='alive-1'), Widget(slug='alive-2'), Widget(slug='doomed')]
    db.add_all(rows)
    await db.flush()
    return rows


async def _slugs(db: AsyncSession, **options) -> list[str]:
    stmt = select(Widget.slug).order_by(Widget.slug)
    if options:
        stmt = stmt.execution_options(**options)
    return list((await db.execute(stmt)).scalars().all())


async def test_deleted_rows_disappear_without_any_filter_in_the_query(widgets, db: AsyncSession):
    """쿼리에 `deleted == 0` 이 **없다.** 그런데도 안 보여야 한다 (규칙 #6)."""
    await db.execute(soft_delete(Widget, Widget.slug == 'doomed'))
    db.expire_all()

    assert await _slugs(db) == ['alive-1', 'alive-2']


async def test_include_deleted_opts_out(widgets, db: AsyncSession):
    await db.execute(soft_delete(Widget, Widget.slug == 'doomed'))
    db.expire_all()

    assert await _slugs(db, include_deleted=True) == ['alive-1', 'alive-2', 'doomed']


async def test_soft_delete_stores_the_row_id_not_a_boolean(widgets, db: AsyncSession):
    """§1.4 의 핵심. 1 이나 True 가 아니라 자기 id 다."""
    target_id = widgets[2].id  # expire_all() 전에 값을 꺼내둔다
    await db.execute(soft_delete(Widget, Widget.id == target_id))
    db.expire_all()

    stored = (
        await db.execute(select(Widget).where(Widget.id == target_id).execution_options(include_deleted=True))
    ).scalar_one()

    assert stored.deleted == target_id
    assert stored.is_deleted is True


async def test_slug_can_be_reused_after_deletion(widgets, db: AsyncSession):
    """이게 `deleted = id` 를 쓰는 이유 전부다."""
    await db.execute(soft_delete(Widget, Widget.slug == 'doomed'))
    db.add(Widget(slug='doomed'))
    await db.flush()

    assert await _slugs(db) == ['alive-1', 'alive-2', 'doomed']


async def test_duplicate_slug_among_live_rows_is_still_rejected(widgets, db: AsyncSession):
    """재사용을 허용하면서 중복은 막아야 한다 — 둘 다 성립해야 의미가 있다."""
    db.add(Widget(slug='alive-1'))

    with pytest.raises(IntegrityError):
        await db.flush()


async def test_get_by_pk_also_respects_the_filter(widgets, db: AsyncSession):
    """`session.get()` 은 select 가 아닌 것처럼 보이지만 SQL 을 낼 때는 내부적으로 select 다."""
    target_id = widgets[2].id
    await db.execute(soft_delete(Widget, Widget.id == target_id))
    db.expunge_all()  # identity map 을 비워야 실제로 쿼리가 나간다

    assert await db.get(Widget, target_id) is None


async def test_filter_does_not_apply_to_objects_already_in_the_identity_map(widgets, db: AsyncSession):
    """알아둘 함정: 전역 필터는 **SQL 이 나갈 때만** 붙는다.

    같은 세션에서 이미 로드한 객체를 `get()` 하면 쿼리 없이 identity map 에서 나온다.
    요청 하나가 한 세션을 쓰는 구조(§1.1)에서는 "삭제 직후 같은 요청 안" 뿐이라
    실무에서 문제가 되기 어렵지만, 삭제 후 재조회를 같은 세션에서 하면 눈에 보인다.
    """
    target_id = widgets[2].id
    await db.execute(soft_delete(Widget, Widget.id == target_id))

    assert await db.get(Widget, target_id) is not None


async def test_timestamps_are_timezone_aware(widgets, db: AsyncSession):
    """SQLite 는 tz 를 버린다. UTCDateTime 이 되돌려 붙인다 (common/db/types.py)."""
    db.expire_all()
    row = (await db.execute(select(Widget).limit(1))).scalar_one()

    assert row.created_at.tzinfo is not None
    assert row.updated_at.tzinfo is not None


async def test_primary_key_autoincrements_on_sqlite(widgets, db: AsyncSession):
    """`BIGINT PRIMARY KEY` 는 SQLite 에서 rowid 별칭이 아니라 자동 증가하지 않는다.

    `BigIntPK` 가 sqlite variant 로 INTEGER 를 렌더링해서 이걸 막는다.
    """
    assert all(row.id is not None and row.id > 0 for row in widgets)
    assert len({row.id for row in widgets}) == len(widgets)
