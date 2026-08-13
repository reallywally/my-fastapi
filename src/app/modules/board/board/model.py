"""게시판(카테고리) 테이블. 관리자가 만든다 (§4.2).

`read_role` / `write_role` 은 **문자열로 둔다.** 역할 모델 — 이름 목록, 계층, 저장
위치 — 은 Phase 5(인증/인가)에서 정한다. 지금 enum 으로 굳히면 그때 마이그레이션을
한 번 더 쓰게 되고, 무엇이 필요한지도 모르는 채로 이름을 정하게 된다.

지금 이 값으로 실제 판정하는 것은 하나뿐이다: `ANONYMOUS` 인가 아닌가 (`deps.py`).
"""

from dataclasses import dataclass
from typing import ClassVar, Final

from sqlalchemy import Boolean, Column, Integer, String, Table, Text, UniqueConstraint

from app.common.db import SoftDeletable, define_table

#: 주체 없이 접근할 수 있다는 뜻. 이 프로젝트가 값으로 판정하는 유일한 역할이다.
ANONYMOUS: Final = 'anonymous'

#: 기본 쓰기 역할. Phase 5 전까지는 "로그인 필요" 와 같은 뜻으로만 쓰인다.
MEMBER: Final = 'member'

board_table: Table = define_table(
    'board',
    Column('slug', String(50), nullable=False),  # URL 식별자: 'notice', 'free'
    Column('name', String(100), nullable=False),
    Column('description', Text, nullable=True),
    Column('read_role', String(50), default=ANONYMOUS, nullable=False),
    Column('write_role', String(50), default=MEMBER, nullable=False),
    Column('allow_comment', Boolean, default=True, nullable=False),
    Column('allow_attachment', Boolean, default=True, nullable=False),
    Column('display_order', Integer, default=0, nullable=False),
    # §1.4 — 삭제 후 slug 재사용 가능
    UniqueConstraint('slug', 'deleted'),
)


@dataclass(slots=True)
class Board(SoftDeletable):
    TABLE: ClassVar[Table] = board_table

    slug: str
    name: str
    description: str | None
    read_role: str
    write_role: str
    allow_comment: bool
    allow_attachment: bool
    display_order: int

    @property
    def is_public(self) -> bool:
        """주체 없이 읽을 수 있는 게시판인가."""
        return self.read_role == ANONYMOUS
