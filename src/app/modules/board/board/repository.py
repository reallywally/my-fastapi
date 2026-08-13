"""쿼리만. 업무 규칙은 없다 (§1.2).

`commit()` 하지 않고 (§1.1), `deleted == 0` 을 손으로 쓰지 않는다 (§2.4).
"""

from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import alive, all_of, one_or_none, select_alive, soft_delete
from app.modules.board.board.model import Board, board_table

#: 수정 가능한 컬럼. `slug` 는 없다 — 바뀌면 링크가 깨진다 (`schema.py` 참조).
UPDATABLE: Final = frozenset(
    {'name', 'description', 'read_role', 'write_role', 'allow_comment', 'allow_attachment', 'display_order'}
)


class BoardRepository:
    @staticmethod
    async def get(db: AsyncConnection, pk: int) -> Board | None:
        result = await db.execute(select_alive(Board).where(board_table.c.id == pk))
        return one_or_none(Board, result)

    @staticmethod
    async def get_by_slug(db: AsyncConnection, slug: str) -> Board | None:
        result = await db.execute(select_alive(Board).where(board_table.c.slug == slug))
        return one_or_none(Board, result)

    @staticmethod
    async def list_all(db: AsyncConnection) -> list[Board]:
        """게시판은 수십 개 규모다. 커서 페이지네이션을 붙이지 않는다.

        글 목록(§4.3)과 다르게 여기는 화면 한 번에 다 뿌리는 값이고, `display_order`
        로 정렬한 전체가 곧 메뉴다.
        """
        statement = select_alive(Board).order_by(board_table.c.display_order, board_table.c.id)
        return all_of(Board, await db.execute(statement))

    @classmethod
    async def insert(
        cls,
        db: AsyncConnection,
        *,
        slug: str,
        name: str,
        description: str | None = None,
        read_role: str,
        write_role: str,
        allow_comment: bool = True,
        allow_attachment: bool = True,
        display_order: int = 0,
    ) -> Board:
        result = await db.execute(
            sa.insert(board_table).values(
                slug=slug,
                name=name,
                description=description,
                read_role=read_role,
                write_role=write_role,
                allow_comment=allow_comment,
                allow_attachment=allow_attachment,
                display_order=display_order,
            )
        )
        row = await cls.get(db, result.inserted_primary_key[0])
        if row is None:  # pragma: no cover - 방금 넣은 행이 같은 트랜잭션에서 사라질 수는 없다
            raise RuntimeError('방금 삽입한 게시판을 다시 읽지 못했다')
        return row

    @classmethod
    async def update(cls, db: AsyncConnection, *, pk: int, changes: dict[str, Any]) -> Board | None:
        unknown = sorted(set(changes) - UPDATABLE)
        if unknown:
            raise ValueError(f'수정할 수 없는 컬럼이다: {unknown}')

        if changes:
            await db.execute(sa.update(board_table).where(board_table.c.id == pk, alive(Board)).values(**changes))
        return await cls.get(db, pk)

    @staticmethod
    async def mark_deleted(db: AsyncConnection, pk: int) -> int:
        """§1.4 — `deleted = id`. 글은 손대지 않는다 (§4.7 과 같은 결)."""
        result = await db.execute(soft_delete(Board, board_table.c.id == pk))
        return result.rowcount


board_repository = BoardRepository()
