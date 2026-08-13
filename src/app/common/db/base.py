"""모든 테이블이 등록되는 `MetaData`.

**ORM 은 쓰지 않는다** (§1.6). `DeclarativeBase` 도, `Session` 도 없다. 테이블은
Core 의 `Table` 로 정의하고, 행은 dataclass 로 받는다 (`common/db/model.py`).
SQLAlchemy 를 남겨둔 이유는 하나다 — **방언 차이를 대신 흡수해주는 계층**이 필요해서다.

제약·인덱스 이름을 규칙으로 고정한다. 이걸 나중에 바꾸면 이미 나간 마이그레이션과
이름이 어긋나서 `alembic check`(§2.3)가 매번 시끄러워진다. **첫 리비전 전에 정해야 한다.**

방언마다 이름 길이 상한이 다르다 — PostgreSQL 63자, MySQL 64자. 규칙이 만드는 이름이
길어질 수 있으니(`uq_%(table_name)s_%(column_0_N_name)s`) 컬럼이 많은 unique 를 만들 때는
`name=` 으로 직접 짧게 주는 편이 안전하다.
"""

from typing import Final

from sqlalchemy import MetaData

NAMING_CONVENTION: Final[dict[str, str]] = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

#: 스키마의 전부. alembic autogenerate 가 보는 것도 이것 하나다 (§2.3).
METADATA: Final = MetaData(naming_convention=NAMING_CONVENTION)
