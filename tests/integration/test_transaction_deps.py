"""§1.1 — `ConnDep` 과 `TxDep` 이 실제로 다르게 동작하는지 실 DB 로 확인한다.

**의존성 제너레이터를 손으로 돌리지 않는다.** `async for` 로 돌리면 루프 본문에서
난 예외가 제너레이터 안으로 전달되지 않아서 트랜잭션이 정상 종료로 착각하고
커밋해버린다. FastAPI 는 `athrow()` 로 던져 넣는다 — 그러니 검증도 FastAPI 를
태워서 해야 한다. 여기서 검증하는 것이 바로 그 예외 전파 경로다.

이 테스트 전용 테이블을 따로 만든다 — `METADATA` 를 오염시키면 autogenerate 가
이 테이블을 잡아버린다.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import ConnDep, TxDep

pytestmark = pytest.mark.asyncio(loop_scope='session')

_metadata = MetaData()
scratch = Table(
    'scratch_tx_probe',
    _metadata,
    Column('id', Integer, primary_key=True),
    Column('note', String(50), nullable=False),
)


def _build_probe_app() -> FastAPI:
    """`ConnDep` / `TxDep` 만 쓰는 최소 앱. 도메인 모듈이 없어도 계약을 검증할 수 있다."""
    app = FastAPI()

    @app.post('/write-with-conn')
    async def write_with_conn(db: ConnDep, note: str) -> dict[str, bool]:
        await db.execute(insert(scratch).values(note=note))
        return {'ok': True}

    @app.post('/write-with-tx')
    async def write_with_tx(db: TxDep, note: str) -> dict[str, bool]:
        await db.execute(insert(scratch).values(note=note))
        return {'ok': True}

    @app.post('/write-with-tx-then-fail')
    async def write_with_tx_then_fail(db: TxDep, note: str) -> dict[str, bool]:
        await db.execute(insert(scratch).values(note=note))
        raise RuntimeError('boom')

    return app


@pytest_asyncio.fixture(loop_scope='session')
async def probe_client(db_connection: AsyncConnection) -> AsyncGenerator[AsyncClient, None]:
    # DDL 도 바깥 트랜잭션 안에서 일어난다 — 롤백되면 테이블째 사라진다.
    await db_connection.run_sync(_metadata.create_all)

    @asynccontextmanager
    async def _pinned() -> AsyncGenerator[AsyncConnection, None]:
        yield db_connection

    app = _build_probe_app()
    app.state.db_source = _pinned

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url='http://probe') as client:
        yield client


async def _rows(db_connection: AsyncConnection) -> list[str]:
    result = await db_connection.execute(select(scratch.c.note))
    return sorted(result.scalars().all())


async def test_conn_dep_does_not_commit(probe_client, db_connection):
    """읽기 의존성은 끝나면 무조건 롤백한다 — 여기로 들어온 쓰기는 밖으로 안 나간다."""
    response = await probe_client.post('/write-with-conn', params={'note': 'from-conn-dep'})

    assert response.status_code == 200
    assert 'from-conn-dep' not in await _rows(db_connection)


async def test_tx_dep_commits_on_success(probe_client, db_connection):
    response = await probe_client.post('/write-with-tx', params={'note': 'from-tx-dep'})

    assert response.status_code == 200
    assert 'from-tx-dep' in await _rows(db_connection)


async def test_tx_dep_rolls_back_on_exception(probe_client, db_connection):
    """예외는 DI 가 잡아 롤백한다 — 서비스가 롤백을 호출하지 않는다."""
    response = await probe_client.post('/write-with-tx-then-fail', params={'note': 'doomed'})

    assert response.status_code == 500
    assert 'doomed' not in await _rows(db_connection)


async def test_connection_survives_a_failed_request(probe_client, db_connection):
    """롤백 뒤에도 같은 연결로 계속 쓸 수 있어야 한다 — 아니면 한 번의 500 이 연결을 오염시킨다."""
    await probe_client.post('/write-with-tx-then-fail', params={'note': 'doomed'})
    response = await probe_client.post('/write-with-tx', params={'note': 'after-failure'})

    assert response.status_code == 200
    assert await _rows(db_connection) == ['after-failure']
