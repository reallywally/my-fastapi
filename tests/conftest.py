"""테스트 인프라 (§2.8).

- 실제 Postgres/Redis 를 testcontainers 로 띄운다. 목이 아니라 진짜다.
- 스키마는 **마이그레이션으로** 만든다. `create_all()` 이 아니다 (§2.3).
- 격리는 truncate 가 아니라 **트랜잭션 롤백**이다. 테스트 하나가 커밋해도 밖으로 안 나간다.

Docker 가 없으면 통합/E2E 는 통째로 skip 된다. 유닛 테스트는 영향받지 않는다 —
`import` 만으로 아무 연결도 열리지 않기 때문이다 (§2.1).

**탈출구:** `TEST_DATABASE_URL` / `TEST_REDIS_URL` 을 주면 컨테이너를 띄우지 않고
그 주소를 쓴다. `docker compose up -d` 로 띄워놓고 돌리거나, Docker 를 못 쓰는
환경(WSL 등)에서 필요하다. **테스트가 스키마를 롤백하므로 개발 DB 를 가리켜도 안전하지만,
운영 DB 를 넣지 마라.**
"""

import os
from collections.abc import AsyncGenerator, Iterator

import docker
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.bootstrap.app import create_app
from app.core.paths import ALEMBIC_INI, MIGRATIONS_DIR

POSTGRES_IMAGE = 'postgres:16-alpine'
REDIS_IMAGE = 'redis:7-alpine'


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


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
def containers() -> Iterator[tuple[str, str]]:
    """(postgres_dsn, redis_dsn) 를 돌려준다."""
    external_db = os.getenv('TEST_DATABASE_URL')
    external_redis = os.getenv('TEST_REDIS_URL')
    if external_db and external_redis:
        yield external_db, external_redis
        return

    if not _docker_available():
        pytest.skip(
            'Docker 데몬이 없다 — 통합/E2E 테스트를 건너뛴다. '
            'TEST_DATABASE_URL / TEST_REDIS_URL 로 외부 인스턴스를 가리킬 수 있다.'
        )

    with (
        PostgresContainer(POSTGRES_IMAGE, driver='asyncpg') as postgres,
        RedisContainer(REDIS_IMAGE) as redis,
    ):
        redis_dsn = f'redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0'
        yield postgres.get_connection_url(), redis_dsn


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def app(containers: tuple[str, str]) -> AsyncGenerator:
    """lifespan 을 돌리지 않고 `app.state` 를 직접 채운다.

    `create_app()` 이 자원을 만들지 않기 때문에 가능한 일이다 (§2.1).
    """
    postgres_dsn, redis_dsn = containers

    application = create_app()
    engine = create_async_engine(postgres_dsn)
    application.state.engine = engine
    application.state.session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    application.state.redis = Redis.from_url(redis_dsn, decode_responses=True)

    await run_migrations(engine)

    try:
        yield application
    finally:
        await application.state.redis.aclose()
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
