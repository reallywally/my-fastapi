"""§1.1 — `SessionDep` 과 `TxDep` 이 실제로 다르게 동작하는지 실 DB 로 확인한다.

모델은 Phase 2 부터 생긴다. 여기서는 이 테스트 전용 테이블을 따로 만든다 —
`Base.metadata` 를 오염시키면 Phase 2 의 autogenerate 가 이 테이블을 잡아버린다.
"""

import pytest
import pytest_asyncio
from sqlalchemy import Column, Integer, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker

from app.common.db.session import get_db, get_db_tx

pytestmark = pytest.mark.asyncio(loop_scope='session')

_metadata = MetaData()
scratch = Table(
    'scratch_tx_probe',
    _metadata,
    Column('id', Integer, primary_key=True),
    Column('note', String(50), nullable=False),
)


class _FakeApp:
    def __init__(self, session_factory) -> None:
        self.state = type('State', (), {'session_factory': session_factory})()


class _FakeRequest:
    def __init__(self, app) -> None:
        self.app = app


@pytest_asyncio.fixture(loop_scope='session')
async def scratch_table(db_connection: AsyncConnection):
    await db_connection.run_sync(_metadata.create_all)
    yield
    # 바깥 트랜잭션이 롤백되므로 별도 drop 은 필요 없다.


async def _rows(db_connection: AsyncConnection) -> list[str]:
    result = await db_connection.execute(select(scratch.c.note))
    return sorted(row[0] for row in result)


def _request(db_connection: AsyncConnection):
    factory = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode='create_savepoint',
    )
    return _FakeRequest(_FakeApp(factory))


async def test_session_dep_does_not_commit(scratch_table, db_connection):
    """읽기 세션은 커밋하지 않는다. flush 한 내용도 세션이 닫히면 사라진다."""
    async for session in get_db(_request(db_connection)):
        await session.execute(scratch.insert().values(note='from-session-dep'))
        await session.flush()

    assert 'from-session-dep' not in await _rows(db_connection)


async def test_tx_dep_commits_on_success(scratch_table, db_connection):
    async for session in get_db_tx(_request(db_connection)):
        await session.execute(scratch.insert().values(note='from-tx-dep'))

    assert 'from-tx-dep' in await _rows(db_connection)


async def test_tx_dep_rolls_back_on_exception(scratch_table, db_connection):
    """예외는 DI 가 잡아 롤백한다 — 서비스가 롤백을 호출하지 않는다."""

    async def _write_then_fail() -> None:
        async for session in get_db_tx(_request(db_connection)):
            await session.execute(scratch.insert().values(note='doomed'))
            raise RuntimeError('boom')

    with pytest.raises(RuntimeError, match='boom'):
        await _write_then_fail()

    assert 'doomed' not in await _rows(db_connection)


async def test_missing_session_factory_raises_instead_of_exiting():
    """§3.3 — lifespan 이 안 돌았으면 예외지, 프로세스 종료가 아니다."""

    class _Empty:
        state = type('State', (), {})()

    with pytest.raises(RuntimeError, match='lifespan'):
        async for _ in get_db(_FakeRequest(_Empty())):
            pass
