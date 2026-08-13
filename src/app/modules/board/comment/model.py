"""댓글 테이블 (§4.2, §4.7).

**`path` 를 두는 이유:** 댓글 트리 정렬을 `ORDER BY path` 한 번으로 끝낸다.
`parent_id` 만 있으면 재귀 CTE 를 돌리고 정렬을 앱에서 다시 해야 한다.

`00000012.00000031` — 8자리로 0을 채운 id 를 점으로 잇는다. 자리수를 고정해야
문자열 정렬이 곧 트리 정렬이 된다 (`12` 와 `120` 을 비교하면 순서가 뒤집힌다).

**깊이는 1단(댓글/대댓글)으로 제한한다.** 무한 뎁스는 화면에서 감당이 안 되고,
서버에서 막지 않으면 데이터가 먼저 망가진다.

**`deleted` 와 `is_removed` 는 다른 개념이고 합치면 안 된다** (§4.7).

- `deleted` — 감사·복구용 soft delete. `alive()` 가 감춘다 (§1.4)
- `is_removed` — 트리 유지용 **묘비**. 자식이 있는 댓글을 감추면 대댓글이 고아가 된다

하나로 합치는 순간 `alive()` 가 자식까지 숨겨버린다.
"""

from dataclasses import dataclass
from typing import ClassVar, Final

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Table, Text

from app.common.db import BigIntPK, SoftDeletable, define_table

#: 댓글/대댓글까지. `depth == MAX_DEPTH` 인 댓글에는 답글을 달 수 없다.
MAX_DEPTH: Final = 1

#: `path` 한 마디의 자리수. 8자리면 99,999,999번째 댓글까지 정렬이 유지된다.
PATH_SEGMENT_WIDTH: Final = 8

#: 마디 구분자. 값에 나올 수 없는 문자여야 한다 — 숫자만 들어가므로 점으로 충분하다.
PATH_SEPARATOR: Final = '.'


def build_path(comment_id: int, *, parent_path: str | None = None) -> str:
    """`부모경로.자기id` 를 만든다. 최상위 댓글이면 자기 id 하나뿐이다."""
    segment = str(comment_id).zfill(PATH_SEGMENT_WIDTH)
    return segment if parent_path is None else f'{parent_path}{PATH_SEPARATOR}{segment}'


comment_table: Table = define_table(
    'comment',
    Column('post_id', BigIntPK, ForeignKey('post.id'), nullable=False),
    Column('author_id', BigIntPK, ForeignKey('user.id'), nullable=False),
    Column('parent_id', BigIntPK, ForeignKey('comment.id'), nullable=True),
    # 삽입 시점에는 자기 id 를 모른다. 넣고 나서 채운다 (`repository.insert` 참조).
    Column('path', String(64), nullable=False),
    Column('depth', Integer, default=0, nullable=False),
    Column('content', Text, nullable=False),
    Column('is_removed', Boolean, default=False, nullable=False),  # 묘비 (§4.7)
    # 트리 조회는 언제나 "이 글의 댓글을 path 순으로" 다.
    Index('ix_comment_thread', 'post_id', 'path'),
)


@dataclass(slots=True)
class Comment(SoftDeletable):
    TABLE: ClassVar[Table] = comment_table

    post_id: int
    author_id: int
    parent_id: int | None
    path: str
    depth: int
    content: str
    is_removed: bool

    @property
    def accepts_replies(self) -> bool:
        """답글을 달 수 있는 댓글인가. 묘비에는 달 수 없다 — 이미 지운 자리다."""
        return self.depth < MAX_DEPTH and not self.is_removed
