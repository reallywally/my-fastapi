"""엔진 생성. **호출은 lifespan 만 한다** (§2.1) — 이 모듈은 함수만 제공한다.

SQLite 는 기본값이 서버 DB 와 다르다. 그냥 `create_async_engine` 만 부르면
조용히 틀린 동작을 얻는다:

- **외래키가 꺼져 있다.** 켜지 않으면 §4 의 FK 가 전부 장식이다.
- **journal_mode 가 delete 다.** WAL 이라야 읽기가 쓰기에 막히지 않는다.
- **잠금 대기가 0 이다.** 동시 쓰기에서 즉시 `database is locked` 가 난다.
- **드라이버가 트랜잭션을 제멋대로 연다.** sqlite3 의 legacy `isolation_level` 때문에
  DDL 앞에서 커밋이 새고 SAVEPOINT 가 어긋난다. 테스트의 롤백 격리(§2.8)가 여기 걸린다.

마지막 항목이 특히 조용하다. 명시적 BEGIN 으로 드라이버의 자동 트랜잭션을 걷어낸다.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def _sqlite_file_path(url: str) -> Path | None:
    """`sqlite+aiosqlite:///./var/app.db` → `./var/app.db`. 메모리 DB 면 None."""
    _, _, location = url.partition('///')
    if not location or location.startswith(':memory:') or 'mode=memory' in location:
        return None
    return Path(location.split('?', 1)[0])


def _install_sqlite_pragmas(engine: Engine, settings: Settings) -> None:
    @event.listens_for(engine, 'connect')
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        # sqlite3 이 알아서 여는 트랜잭션을 끈다. BEGIN 은 아래에서 직접 낸다.
        dbapi_connection.isolation_level = None

        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f'PRAGMA foreign_keys={"ON" if settings.db_foreign_keys else "OFF"}')
            cursor.execute(f'PRAGMA journal_mode={settings.db_journal_mode.value}')
            cursor.execute(f'PRAGMA busy_timeout={settings.db_busy_timeout_ms}')
            # WAL 에서 fsync 를 매번 하지 않는다. 프로세스 크래시에는 안전하다.
            cursor.execute('PRAGMA synchronous=NORMAL')
        finally:
            cursor.close()

    @event.listens_for(engine, 'begin')
    def _begin(connection: Any) -> None:
        connection.exec_driver_sql('BEGIN')


def create_engine(settings: Settings) -> AsyncEngine:
    """설정에서 엔진을 만든다. 연결은 아직 열리지 않는다 (lazy)."""
    if settings.is_sqlite:
        db_file = _sqlite_file_path(settings.database_url)
        if db_file is not None:
            # 디렉터리가 없으면 sqlite 는 만들어주지 않고 그냥 실패한다.
            db_file.parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(settings.database_url, echo=settings.db_echo)

    if settings.is_sqlite:
        _install_sqlite_pragmas(engine.sync_engine, settings)

    return engine
