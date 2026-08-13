"""엔진 팩토리의 방언 분기 (`common/db/engine.py`). DB 없이 옵션만 본다.

실제 SQLite 동작(PRAGMA, 롤백 격리)은 `tests/integration/test_sqlite_engine.py` 에 있다.
"""

import pytest
from pydantic import ValidationError

from app.common.db.engine import _pool_options, _sqlite_file_path
from app.core.config import SUPPORTED_DRIVERS, Settings

POSTGRES_URL = 'postgresql+psycopg://app:app@localhost:5432/app'
MYSQL_URL = 'mysql+asyncmy://app:app@localhost:3306/app'


def test_sqlite_gets_no_pool_options():
    """SQLite 는 파일이다. 풀 인자를 넘기면 엔진 생성 자체가 실패한다."""
    assert _pool_options(Settings()) == {}


@pytest.mark.parametrize('url', [POSTGRES_URL, MYSQL_URL])
def test_server_databases_get_pool_options(url: str):
    """`pool_pre_ping` 이 없으면 방화벽이 끊은 죽은 연결을 다음 요청에서 처음 안다."""
    options = _pool_options(Settings(database_url=url, db_pool_size=7))

    assert options['pool_size'] == 7
    assert options['pool_pre_ping'] is True
    assert options['pool_recycle'] > 0


def test_dialect_is_read_from_the_url():
    assert Settings().dialect == 'sqlite'
    assert Settings(database_url=POSTGRES_URL).dialect == 'postgresql'
    assert Settings(database_url=MYSQL_URL).dialect == 'mysql'
    assert Settings(database_url=MYSQL_URL).is_sqlite is False


@pytest.mark.parametrize('url', sorted(f'{driver}://user:pw@host/db' for driver in SUPPORTED_DRIVERS))
def test_every_supported_driver_is_accepted(url: str):
    """지원한다고 적어둔 드라이버가 실제로 통과하는지 — 목록과 검증이 갈라지면 안 된다."""
    assert Settings(database_url=url).database_url == url


def test_a_sync_driver_is_rejected():
    """동기 드라이버를 넣으면 기동은 되고 첫 쿼리에서 죽는다."""
    with pytest.raises(ValidationError, match='async 드라이버'):
        Settings(database_url='sqlite:///./var/app.db')


def test_an_untested_driver_is_rejected():
    """목록에 없는 드라이버는 거부한다. 지원한다고 말한 적 없는 방언으로 기동하면 안 된다."""
    with pytest.raises(ValidationError, match='지원하지 않는 드라이버'):
        Settings(database_url='oracle+oracledb://user:pw@host/db')


def test_memory_urls_have_no_file_path():
    assert _sqlite_file_path('sqlite+aiosqlite:///:memory:') is None
    assert _sqlite_file_path('sqlite+aiosqlite://') is None
