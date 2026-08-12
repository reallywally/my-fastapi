"""alembic 실행 환경.

마이그레이션은 스키마의 **유일한** 소스다 (§2.3). 앱은 `create_all()` 을 하지 않는다.

세 가지 경로를 지원한다:
- offline  : `alembic upgrade head --sql` — SQL 만 뽑는다
- online   : 설정에서 URL 을 읽어 직접 접속 (배포 단계에서 실행)
- injected : 테스트가 `config.attributes['connection']` 으로 살아 있는 연결을 꽂는다
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.common.db import Base
from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_dsn


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_database_url(), literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    injected = config.attributes.get('connection')
    if injected is not None:
        # 테스트가 꽂아준 동기 Connection. 새 엔진을 만들지 않는다.
        do_run_migrations(injected)
        return
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
