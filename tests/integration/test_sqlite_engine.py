"""SQLite 의 기본값을 실제로 고쳐놨는지 확인한다 (`common/db/engine.py`).

전부 "설정 안 하면 조용히 틀리는" 것들이다. 특히 외래키 — 꺼져 있으면 FK 를 선언해도
아무 일도 일어나지 않고, 그걸 알아채는 시점은 보통 데이터가 이미 깨진 뒤다.

방언 분기 자체(풀 옵션, 드라이버 검증)는 `tests/unit/test_engine_options.py` 가 본다.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.db.engine import create_engine
from app.core.config import Settings


async def _pragma(engine: AsyncEngine, name: str):
    async with engine.connect() as connection:
        return (await connection.execute(text(f'PRAGMA {name}'))).scalar()


@pytest.mark.asyncio(loop_scope='session')
async def test_foreign_keys_are_enforced(app):
    """SQLite 기본값은 OFF 다."""
    assert await _pragma(app.state.engine, 'foreign_keys') == 1


@pytest.mark.asyncio(loop_scope='session')
async def test_busy_timeout_is_set(app):
    """0 이면 동시 쓰기에서 대기 없이 'database is locked' 가 난다."""
    assert await _pragma(app.state.engine, 'busy_timeout') > 0


@pytest.mark.asyncio(loop_scope='session')
async def test_foreign_key_violation_actually_raises(app):
    async with app.state.engine.begin() as connection:
        await connection.execute(text('CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)'))
        await connection.execute(
            text('CREATE TABLE fk_child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES fk_parent(id))')
        )

    try:
        with pytest.raises(IntegrityError):
            async with app.state.engine.begin() as connection:
                await connection.execute(text('INSERT INTO fk_child (id, parent_id) VALUES (1, 999)'))
    finally:
        async with app.state.engine.begin() as connection:
            await connection.execute(text('DROP TABLE fk_child'))
            await connection.execute(text('DROP TABLE fk_parent'))


@pytest.mark.asyncio(loop_scope='session')
async def test_ddl_rolls_back_inside_a_transaction(db_connection):
    """명시적 BEGIN 이 없으면 sqlite3 드라이버가 DDL 앞에서 커밋을 흘린다.

    그러면 테스트 격리(§2.8)가 깨진다 — 한 테스트가 만든 테이블이 다음 테스트에 남는다.
    """
    await db_connection.execute(text('CREATE TABLE rollback_probe (id INTEGER PRIMARY KEY)'))
    found = await db_connection.execute(text("SELECT count(*) FROM sqlite_master WHERE name = 'rollback_probe'"))

    assert found.scalar() == 1
    # 이 연결의 바깥 트랜잭션은 fixture 가 롤백한다. 다음 테스트에서 남아 있으면 안 된다.


@pytest.mark.asyncio(loop_scope='session')
async def test_previous_tests_ddl_did_not_survive(db_connection):
    found = await db_connection.execute(text("SELECT count(*) FROM sqlite_master WHERE name = 'rollback_probe'"))

    assert found.scalar() == 0


@pytest.mark.asyncio(loop_scope='session')
async def test_engine_creates_the_parent_directory(tmp_path):
    """디렉터리가 없으면 sqlite 는 만들어주지 않고 그냥 실패한다."""
    target = tmp_path / 'nested' / 'deeper' / 'app.db'
    engine = create_engine(Settings(database_url=f'sqlite+aiosqlite:///{target}'))
    try:
        async with engine.connect() as connection:
            await connection.execute(text('SELECT 1'))
    finally:
        await engine.dispose()

    assert target.parent.is_dir()
