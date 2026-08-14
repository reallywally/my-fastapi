"""쿼리만. 업무 규칙은 없다 (§1.2).

`commit()` 하지 않고 (§1.1), `deleted == 0` 을 손으로 쓰지 않는다 (§2.4).

**삽입이 두 문장인 이유:** `path` 는 자기 id 를 담는데 (§4.2) 그 id 는 INSERT 전에
알 수 없다. 넣고 나서 채운다. 둘 다 호출자의 트랜잭션 안이라 중간 상태가 밖으로
새지 않는다 — `path` 가 빈 행이 보이는 순간은 없다.
"""

from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import alive, all_of, one_or_none, select_alive, soft_delete
from app.modules.board.comment.model import Comment, build_path, comment_table

#: 수정 가능한 컬럼. `parent_id` 와 `path` 는 없다 — 트리 구조를 바꾸는 것은
#: 수정이 아니라 이동이고, 그건 별개의 동작이다.
UPDATABLE: Final = frozenset({'content'})


class CommentRepository:
    @staticmethod
    async def get(db: AsyncConnection, pk: int) -> Comment | None:
        result = await db.execute(select_alive(Comment).where(comment_table.c.id == pk))
        return one_or_none(Comment, result)

    @staticmethod
    def _thread_stmt(post_id: int, cursor: str | None, size: int) -> Select[Any]:
        """§4.2 — `ORDER BY path` 한 번이 곧 트리 정렬이다.

        커서도 `path` 다. 정렬 키가 곧 커서라야 keyset 이 성립한다 — `id` 로 자르면
        부모와 자식 사이가 끊긴다.
        """
        stmt = select_alive(Comment).where(comment_table.c.post_id == post_id)
        if cursor is not None:
            stmt = stmt.where(comment_table.c.path > cursor)
        return stmt.order_by(comment_table.c.path).limit(size + 1)

    @classmethod
    async def list_thread(cls, db: AsyncConnection, *, post_id: int, cursor: str | None, size: int) -> list[Comment]:
        return all_of(Comment, await db.execute(cls._thread_stmt(post_id, cursor, size)))

    @staticmethod
    async def count_children(db: AsyncConnection, pk: int) -> int:
        """살아 있는 자식 댓글 수. 삭제 방식을 가르는 값이다 (§4.7).

        묘비(`is_removed`)도 자식으로 센다 — 화면에 남아 있는 이상 부모를 감추면
        고아가 된다.
        """
        statement = (
            sa.select(sa.func.count()).select_from(comment_table).where(comment_table.c.parent_id == pk, alive(Comment))
        )
        return (await db.execute(statement)).scalar_one()

    @staticmethod
    async def count_alive_by_post(db: AsyncConnection, post_ids: list[int]) -> dict[int, int]:
        """글마다 살아 있는 댓글 수. 보정 배치가 쓴다 (§4.4).

        기준이 `comment_count` 와 같아야 한다 — **묘비도 한 개로 센다** (§4.7).
        여기서 빼면 배치가 돌 때마다 화면의 개수가 줄어든다.

        결과에 없는 글은 댓글이 0개다. 0 을 돌려주려고 빈 행을 만들지 않는다.
        """
        if not post_ids:
            return {}
        statement = (
            sa.select(comment_table.c.post_id, sa.func.count())
            .where(comment_table.c.post_id.in_(post_ids), alive(Comment))
            .group_by(comment_table.c.post_id)
        )
        return dict((await db.execute(statement)).all())  # type: ignore[arg-type]

    @classmethod
    async def insert(
        cls,
        db: AsyncConnection,
        *,
        post_id: int,
        author_id: int,
        content: str,
        parent_id: int | None = None,
        parent_path: str | None = None,
        depth: int = 0,
    ) -> Comment:
        """넣고, `path` 를 채우고, 다시 읽는다."""
        result = await db.execute(
            sa.insert(comment_table).values(
                post_id=post_id,
                author_id=author_id,
                parent_id=parent_id,
                path='',  # 아래에서 자기 id 로 채운다
                depth=depth,
                content=content,
            )
        )
        pk = result.inserted_primary_key[0]
        await db.execute(
            sa.update(comment_table)
            .where(comment_table.c.id == pk)
            .values(path=build_path(pk, parent_path=parent_path))
        )

        row = await cls.get(db, pk)
        if row is None:  # pragma: no cover - 방금 넣은 행이 같은 트랜잭션에서 사라질 수는 없다
            raise RuntimeError('방금 삽입한 댓글을 다시 읽지 못했다')
        return row

    @classmethod
    async def update(cls, db: AsyncConnection, *, pk: int, changes: dict[str, Any]) -> Comment | None:
        unknown = sorted(set(changes) - UPDATABLE)
        if unknown:
            raise ValueError(f'수정할 수 없는 컬럼이다: {unknown}')

        if changes:
            await db.execute(sa.update(comment_table).where(comment_table.c.id == pk, alive(Comment)).values(**changes))
        return await cls.get(db, pk)

    @staticmethod
    async def mark_removed(db: AsyncConnection, pk: int) -> int:
        """§4.7 — **묘비.** soft delete 가 아니다.

        자식이 있는 댓글을 `deleted` 로 감추면 대댓글이 고아가 된다. 행은 트리에
        남기고 내용만 지운다 — 마스킹은 응답 스키마가 한다.
        """
        result = await db.execute(
            sa.update(comment_table).where(comment_table.c.id == pk, alive(Comment)).values(is_removed=True, content='')
        )
        return result.rowcount

    @staticmethod
    async def mark_deleted(db: AsyncConnection, pk: int) -> int:
        """§1.4 — `deleted = id`. 자식이 없을 때만 쓴다 (§4.7)."""
        result = await db.execute(soft_delete(Comment, comment_table.c.id == pk))
        return result.rowcount


comment_repository = CommentRepository()
