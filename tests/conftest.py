"""테스트 인프라 (§2.8).

- **DB 는 진짜 SQLite 다.** 파일로 만들고 테스트가 끝나면 지운다. 목이 아니다.
- 스키마는 **마이그레이션으로** 만든다. `create_all()` 이 아니다 (§2.3).
- 격리는 truncate 가 아니라 **트랜잭션 롤백**이다. 테스트가 커밋해도 밖으로 안 나간다.
- Redis 는 `fakeredis` 로 대체한다. §2.1 이 예고한 그대로 — `app.state` 를 바꿔
  끼우면 실제 Redis 없이 돈다. 진짜 Redis 로 돌리려면 `TEST_REDIS_URL` 을 준다.

Docker 가 필요 없다. 유닛·통합·E2E 전부 아무 데서나 돈다.
"""

import os
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.bootstrap.app import create_app
from app.common.db.engine import create_engine
from app.core.config import Settings, get_settings
from app.core.constants import JournalMode
from app.core.paths import ALEMBIC_INI, MIGRATIONS_DIR


async def run_migrations(engine: AsyncEngine) -> None:
    """`alembic upgrade head`. 살아 있는 연결을 env.py 에 꽂아준다."""

    def _upgrade(connection: object) -> None:
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option('script_location', str(MIGRATIONS_DIR))
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, 'head')

    async with engine.begin() as connection:
        await connection.run_sync(_upgrade)


@pytest.fixture(scope='session')
def settings() -> Iterator[Settings]:
    """테스트용 설정. 임시 디렉터리의 SQLite 파일을 가리킨다.

    `Settings(...)` 를 따로 만들지 않고 **환경변수를 덮어쓴 뒤 `get_settings()` 를 쓴다.**
    앱 내부(예외 핸들러 등)도 `get_settings()` 를 보기 때문에, 둘을 따로 만들면
    테스트가 보는 설정과 앱이 보는 설정이 갈라진다.
    """
    with TemporaryDirectory(prefix='my-fastapi-test-') as tmp:
        overrides = {
            'ENVIRONMENT': 'test',
            'DATABASE_URL': f'sqlite+aiosqlite:///{Path(tmp) / "test.db"}',
            # 임시 파일이라 크래시 내구성이 필요 없다. WAL 보조 파일도 안 남는다.
            'DB_JOURNAL_MODE': JournalMode.memory.value,
            'REDIS_URL': os.getenv('TEST_REDIS_URL', 'redis://localhost:6379/15'),
            'JWT_SECRET': 'test-secret-not-used-in-production-32b',
        }
        saved = {key: os.environ.get(key) for key in overrides}
        os.environ.update(overrides)
        get_settings.cache_clear()
        try:
            yield get_settings()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            get_settings.cache_clear()


@pytest.fixture(scope='session')
def redis_client(settings: Settings):
    """기본은 fakeredis. `TEST_REDIS_URL` 이 있으면 진짜 서버를 쓴다."""
    if os.getenv('TEST_REDIS_URL'):
        return Redis.from_url(settings.redis_dsn, decode_responses=True)
    return FakeAsyncRedis(decode_responses=True)


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def app(settings: Settings, redis_client) -> AsyncGenerator:
    """lifespan 을 돌리지 않고 `app.state` 를 직접 채운다.

    `create_app()` 이 자원을 만들지 않기 때문에 가능한 일이다 (§2.1).
    """
    application = create_app()
    engine = create_engine(settings)
    application.state.engine = engine
    application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    application.state.redis = redis_client

    await run_migrations(engine)

    try:
        yield application
    finally:
        await redis_client.aclose()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope='session')
async def db_connection(app) -> AsyncGenerator[AsyncConnection, None]:
    """테스트 하나를 감싸는 바깥 트랜잭션. 끝나면 무조건 롤백한다."""
    async with app.state.engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest_asyncio.fixture(loop_scope='session')
async def db(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """repository 통합 테스트용 세션.

    `join_transaction_mode='create_savepoint'` 덕분에 세션이 `commit()` 해도
    바깥 트랜잭션은 살아 있고, 테스트 종료 시 통째로 롤백된다.
    """
    session = AsyncSession(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode='create_savepoint',
    )
    try:
        yield session
    finally:
        await session.close()


@pytest_asyncio.fixture(loop_scope='session')
async def client(app, db_connection: AsyncConnection) -> AsyncGenerator[AsyncClient, None]:
    """E2E 클라이언트. 라우터가 쓰는 세션 팩토리를 테스트 트랜잭션에 묶는다."""
    original = app.state.session_factory
    app.state.session_factory = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode='create_savepoint',
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http_client:
            yield http_client
    finally:
        app.state.session_factory = original
