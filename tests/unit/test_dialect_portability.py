"""§1.6 — "나중에 PostgreSQL/MySQL 로 바꿀 수 있다" 를 **사실로** 만드는 테스트.

지금 돌리는 DB 는 SQLite 하나다. 그러면 이식성은 보통 주장으로만 남고, 실제로 옮기는
날 처음 깨진다. 여기서는 서버를 띄우지 않고 그 주장을 검증한다 — SQLAlchemy 는
드라이버 없이도 **컴파일**을 할 수 있다. 방언에 없는 구문을 쓰면 컴파일 단계에서
`UnsupportedCompilationError` 나 `CompileError` 가 난다.

이게 잡아주는 것:
- 방언 하나에만 있는 구문 (`RETURNING`, `ON CONFLICT`, ...)
- 길이 없는 `String` — MySQL 은 `VARCHAR` 에 길이가 필수다
- `CREATE TABLE` DDL 자체가 렌더링되지 않는 타입

이게 못 잡는 것: 런타임 의미 차이(잠금, 격리 수준, 정렬·대소문자 비교, 타임존).
그건 실제로 그 DB 로 한 번 돌려야 안다. 그래서 이 파일은 **하한선**이지 보증이 아니다.
"""

import pytest
from sqlalchemy import Dialect
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable

from app.bootstrap.models import MODELS
from app.common.db import select_alive, soft_delete
from app.modules.user.model import User, user_table
from app.modules.user.repository import UserRepository

#: `core/config.py` 의 `SUPPORTED_DRIVERS` 가 말하는 방언들.
DIALECTS: dict[str, Dialect] = {
    'sqlite': sqlite.dialect(),
    'postgresql': postgresql.dialect(),
    'mysql': mysql.dialect(),
}


def _statements() -> dict[str, object]:
    """레포지토리가 실제로 내는 문장들. 새 쿼리를 추가하면 여기도 추가한다."""
    return {
        'select_alive': select_alive(User).where(user_table.c.id == 1),
        'page_first': UserRepository._page_stmt(None, 10),
        'page_next': UserRepository._page_stmt(100, 10),
        'insert': user_table.insert().values(username='x', email='x@example.com'),
        'update': user_table.update().where(user_table.c.id == 1).values(nickname='x'),
        'soft_delete': soft_delete(User, user_table.c.id == 1),
    }


@pytest.mark.parametrize('dialect_name', sorted(DIALECTS))
@pytest.mark.parametrize('statement_name', sorted(_statements()))
def test_every_repository_statement_compiles(statement_name: str, dialect_name: str):
    """레포지토리의 모든 문장이 세 방언에서 컴파일된다."""
    statement = _statements()[statement_name]

    compiled = statement.compile(dialect=DIALECTS[dialect_name])

    assert str(compiled).strip(), f'{statement_name} 이 {dialect_name} 에서 빈 SQL 로 컴파일됐다'


@pytest.mark.parametrize('dialect_name', sorted(DIALECTS))
def test_every_table_renders_ddl(dialect_name: str):
    """`CREATE TABLE` 이 세 방언에서 만들어진다.

    MySQL 은 길이 없는 `VARCHAR` 를 렌더링하지 못한다 — 그런 컬럼이 하나라도 있으면
    여기서 잡힌다. 마이그레이션을 그 방언에 대고 돌려보기 전에 알 수 있는 유일한 지점이다.
    """
    for model in MODELS:
        ddl = str(CreateTable(model.TABLE).compile(dialect=DIALECTS[dialect_name]))

        assert model.TABLE.name in ddl


def test_primary_key_autoincrements_on_every_dialect():
    """자동 증가 PK 는 방언마다 문법이 다르다. 그 차이를 `BigIntPK` 가 흡수한다 (§1.6).

    SQLite 에서 `BIGINT PRIMARY KEY` 는 rowid 별칭이 **아니라서** 자동 증가하지 않는다 —
    id 를 손으로 안 넣으면 NULL 이 들어간다. 그래서 sqlite 에서만 INTEGER 로 내려간다.
    """
    rendered = {name: str(CreateTable(user_table).compile(dialect=dialect)) for name, dialect in DIALECTS.items()}

    assert 'id INTEGER NOT NULL' in rendered['sqlite']
    assert 'id BIGSERIAL NOT NULL' in rendered['postgresql']
    assert 'id BIGINT NOT NULL AUTO_INCREMENT' in rendered['mysql']


def test_status_is_not_a_native_enum():
    """방언마다 enum 이 다르다 — VARCHAR + CHECK 로 통일해야 세 방언에서 같은 DDL 이 나온다."""
    for name, dialect in DIALECTS.items():
        ddl = str(CreateTable(user_table).compile(dialect=dialect))

        assert 'VARCHAR(20)' in ddl, f'{name}: status 가 VARCHAR 가 아니다'
        assert 'CREATE TYPE' not in ddl, f'{name}: 방언 전용 enum 타입을 만든다'


def test_timestamps_render_on_every_dialect():
    """`UTCDateTime` 은 방언마다 다른 타입으로 내려간다 (`common/db/types.py`)."""
    rendered = {name: str(CreateTable(user_table).compile(dialect=dialect)) for name, dialect in DIALECTS.items()}

    assert 'created_at DATETIME' in rendered['sqlite']
    assert 'created_at TIMESTAMP WITH TIME ZONE' in rendered['postgresql']
    assert 'created_at DATETIME' in rendered['mysql']
