"""쿼리만. 업무 규칙은 없다 (§1.2).

`commit()` 하지 않고 (§1.1), `deleted == 0` 을 손으로 쓰지 않는다 (§2.4).

**목록은 `OFFSET` 을 쓰지 않는다** (§4.3). 게시판은 깊은 페이지가 흔하고,
`OFFSET 100000` 은 10만 행을 읽고 버린다. 커서는 마지막으로 본 id 다.
"""

from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import alive, all_of, one_or_none, select_alive, soft_delete
from app.modules.board.post.model import Post, PostStatus, post_table

#: 수정 가능한 컬럼. `board_id` 는 없다 — 옮기기는 권한이 달라지는 동작이다 (`schema.py`).
UPDATABLE: Final = frozenset({'title', 'content', 'status'})


class PostRepository:
    @staticmethod
    async def get(db: AsyncConnection, pk: int) -> Post | None:
        result = await db.execute(select_alive(Post).where(post_table.c.id == pk))
        return one_or_none(Post, result)

    @staticmethod
    def _published(board_id: int) -> Select[Any]:
        """공개된 글만. 초안은 목록에도 검색에도 나오지 않는다."""
        return select_alive(Post).where(
            post_table.c.board_id == board_id,
            post_table.c.status == PostStatus.published,
        )

    @classmethod
    def _page_stmt(cls, board_id: int, cursor: int | None, size: int) -> Select[Any]:
        """§4.3 — `size + 1` 을 읽어서 `has_next` 를 판정한다. `COUNT(*)` 는 돌리지 않는다.

        **고정글은 여기서 뺀다.** 정렬 키에 `is_pinned` 를 섞으면 커서가 깨진다 —
        고정글은 별도 쿼리로 앞에 붙인다 (`list_pinned`).
        """
        stmt = (
            cls._published(board_id)
            .where(post_table.c.is_pinned.is_(False))
            .order_by(post_table.c.id.desc())
            .limit(size + 1)
        )
        if cursor is not None:
            stmt = stmt.where(post_table.c.id < cursor)
        return stmt

    @classmethod
    async def list_page(cls, db: AsyncConnection, *, board_id: int, cursor: int | None, size: int) -> list[Post]:
        return all_of(Post, await db.execute(cls._page_stmt(board_id, cursor, size)))

    @classmethod
    async def list_pinned(cls, db: AsyncConnection, *, board_id: int) -> list[Post]:
        """고정글 전체. 커서와 무관하게 첫 페이지에만 붙는다 (§4.3).

        상한을 두지 않는 이유: 고정글이 수십 개가 되는 게시판은 운영 문제지 쿼리 문제가
        아니다. 그 상황을 여기서 조용히 잘라내면 관리자가 왜 안 보이는지 알 수 없다.
        """
        statement = cls._published(board_id).where(post_table.c.is_pinned.is_(True)).order_by(post_table.c.id.desc())
        return all_of(Post, await db.execute(statement))

    @classmethod
    async def insert(
        cls,
        db: AsyncConnection,
        *,
        board_id: int,
        author_id: int,
        title: str,
        content: str,
        status: PostStatus = PostStatus.published,
        is_pinned: bool = False,
    ) -> Post:
        result = await db.execute(
            sa.insert(post_table).values(
                board_id=board_id,
                author_id=author_id,
                title=title,
                content=content,
                status=status,
                is_pinned=is_pinned,
            )
        )
        row = await cls.get(db, result.inserted_primary_key[0])
        if row is None:  # pragma: no cover - 방금 넣은 행이 같은 트랜잭션에서 사라질 수는 없다
            raise RuntimeError('방금 삽입한 글을 다시 읽지 못했다')
        return row

    @classmethod
    async def update(cls, db: AsyncConnection, *, pk: int, changes: dict[str, Any]) -> Post | None:
        unknown = sorted(set(changes) - UPDATABLE)
        if unknown:
            raise ValueError(f'수정할 수 없는 컬럼이다: {unknown}')

        if changes:
            await db.execute(sa.update(post_table).where(post_table.c.id == pk, alive(Post)).values(**changes))
        return await cls.get(db, pk)

    @staticmethod
    async def mark_deleted(db: AsyncConnection, pk: int) -> int:
        """§1.4 — `deleted = id`. 댓글은 손대지 않는다 (§4.7)."""
        result = await db.execute(soft_delete(Post, post_table.c.id == pk))
        return result.rowcount

    @staticmethod
    async def bump_comment_count(db: AsyncConnection, pk: int, delta: int) -> None:
        """§4.4 — **DB 에서 더한다.** 앱에서 읽고 더해서 쓰면 동시 댓글에서 갱신이 유실된다.

        댓글 모듈은 Phase 4 의 다음 항목이지만, 이 쿼리는 `post` 가 소유한다 —
        `comment` 가 `post` 테이블을 직접 건드리면 §4.1 의 의존 방향이 역류한다.
        """
        await db.execute(
            sa.update(post_table)
            .where(post_table.c.id == pk, alive(Post))
            .values(comment_count=post_table.c.comment_count + delta)
        )


post_repository = PostRepository()
