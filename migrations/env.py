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
from sqlalchemy.engine import Connection

from app.bootstrap import models  # noqa: F401 — import 부작용으로 모델을 metadata 에 등록한다
from app.common.db import Base
from app.common.db.engine import create_engine
from app.common.db.types import UTCDateTime
from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """커스텀 타입을 렌더링하면서 **import 도 같이 넣는다.**

    이걸 안 하면 autogenerate 가 `app.common.db.types.UTCDateTime(...)` 이라고만 쓰고
    import 는 빠뜨린다. 리비전 파일은 문법적으로 멀쩡하고, `alembic upgrade` 를
    실행하는 순간 `NameError` 로 죽는다 — 즉 배포 시점에 처음 안다.
    """
    if type_ == 'type' and isinstance(obj, UTCDateTime):
        autogen_context.imports.add('from app.common.db.types import UTCDateTime')  # type: ignore[attr-defined]
        return 'UTCDateTime()'
    return False


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        render_item=_render_item,
        # SQLite 는 ALTER TABLE 이 거의 없다. batch 모드가 임시 테이블로 복사·교체한다.
        # 이걸 안 켜면 컬럼 변경/삭제 리비전이 실행 시점에 죽는다.
        render_as_batch=get_settings().is_sqlite,
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
    """엔진은 앱과 **같은 팩토리**로 만든다.

    `create_async_engine` 을 직접 부르면 SQLite 파일의 상위 디렉터리 생성과 PRAGMA
    (특히 `foreign_keys`) 가 빠진다. 마이그레이션이 FK 를 만드는데 정작 FK 가 꺼진
    연결에서 도는 상황이 된다.
    """
    engine = create_engine(get_settings())
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
