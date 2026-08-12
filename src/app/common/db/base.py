"""모든 모델의 공통 조상.

제약·인덱스 이름을 규칙으로 고정한다. 이걸 나중에 바꾸면 이미 나간 마이그레이션과
이름이 어긋나서 `alembic check`(§2.3)가 매번 시끄러워진다. **첫 리비전 전에 정해야 한다.**

`DateTimeMixin` / `SoftDeleteMixin` 은 Phase 2 에서 이 파일 옆에 붙는다.
"""

from typing import Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: Final[dict[str, str]] = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
