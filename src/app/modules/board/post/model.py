"""게시글 테이블 (§4.2).

`view_count` / `comment_count` 는 **비정규화된 값**이다. 목록에서 글마다 세면 N+1 이라
행에 들고 있는다 (§4.4, §4.5). 갱신 규칙이 서로 다르다:

- `comment_count` — 댓글 생성·삭제와 **같은 트랜잭션**에서 `= comment_count + 1` (§4.4)
- `view_count` — Redis 에 버퍼링하고 주기적으로 반영한다. 조회가 쓰기가 되면 안 된다 (§4.5)

둘 다 Phase 4 의 뒤쪽 항목이라 지금은 컬럼만 있고 0 에서 움직이지 않는다.

FK 컬럼에 `BigIntPK` 를 쓰는 것에 주의한다. PK 가 방언마다 다른 타입으로 렌더링되므로
(§1.6) FK 도 같은 타입이어야 한다 — 다르면 제약 생성이 그 방언에서 실패한다.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Boolean, Column, Enum, ForeignKey, Index, Integer, String, Table, Text

from app.common.db import BigIntPK, SoftDeletable, define_table


class PostStatus(StrEnum):
    #: 작성자만 볼 수 있다. 목록·검색에서 빠진다.
    draft = 'draft'
    published = 'published'


post_table: Table = define_table(
    'post',
    Column('board_id', BigIntPK, ForeignKey('board.id'), nullable=False),
    Column('author_id', BigIntPK, ForeignKey('user.id'), nullable=False),
    Column('title', String(200), nullable=False),
    Column('content', Text, nullable=False),
    Column('is_pinned', Boolean, default=False, nullable=False),
    Column(
        'status',
        Enum(PostStatus, name='post_status', native_enum=False, length=20),
        default=PostStatus.published,
        nullable=False,
    ),
    Column('view_count', Integer, default=0, nullable=False),
    Column('comment_count', Integer, default=0, nullable=False),
    # 목록 커서 (§4.3). `deleted` 가 가운데 있는 이유: 조회는 항상 alive() 를 깔고
    # board_id 로 좁힌 뒤 id 로 정렬한다 — 그 순서 그대로여야 인덱스를 탄다.
    Index('ix_post_list', 'board_id', 'deleted', 'id'),
    Index('ix_post_author', 'author_id', 'deleted'),
)


@dataclass(slots=True)
class Post(SoftDeletable):
    TABLE: ClassVar[Table] = post_table

    board_id: int
    author_id: int
    title: str
    content: str
    is_pinned: bool
    status: PostStatus
    view_count: int
    comment_count: int

    @property
    def is_draft(self) -> bool:
        return self.status is PostStatus.draft
