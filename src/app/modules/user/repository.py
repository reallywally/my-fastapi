"""쿼리만. 업무 규칙은 없다 (§1.2).

두 가지를 절대 하지 않는다:
- **`commit()`** — 트랜잭션은 엔드포인트가 결정한다 (§1.1, 규칙 #1).
- **`deleted == 0`** — `select_alive()` 가 붙인다 (§2.4, 규칙 #6). 여기 쓰면 중복이다.

**`RETURNING` 을 쓰지 않는다.** SQLite 3.35+ 와 PostgreSQL 은 지원하지만 MySQL 은
없다 (§1.6). 대신 `inserted_primary_key` 로 id 를 받고 다시 읽는다 — SQLAlchemy 가
방언별로 lastrowid / RETURNING 중 맞는 것을 골라준다. 왕복이 한 번 늘어나는 것이
방언 교체 가능성의 값이다.

`crud` 가 아니라 `repository` 인 이유: CRUD 5개가 아니라 이 모듈의 모든 쿼리가 여기 산다.
"""

from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import alive, all_of, one_or_none, select_alive, soft_delete
from app.modules.user.model import User, UserStatus, user_table

#: 수정 가능한 컬럼. 스키마(`UpdateUser`)가 이미 거르지만, SET 절이 요청 본문에서 온
#: 이름으로 만들어지므로 여기서 한 번 더 못박는다.
UPDATABLE: Final = frozenset({'email', 'nickname'})


class UserRepository:
    @staticmethod
    async def get(db: AsyncConnection, pk: int) -> User | None:
        result = await db.execute(select_alive(User).where(user_table.c.id == pk))
        return one_or_none(User, result)

    @staticmethod
    async def get_by_username(db: AsyncConnection, username: str) -> User | None:
        result = await db.execute(select_alive(User).where(user_table.c.username == username))
        return one_or_none(User, result)

    @staticmethod
    async def get_by_email(db: AsyncConnection, email: str) -> User | None:
        result = await db.execute(select_alive(User).where(user_table.c.email == email))
        return one_or_none(User, result)

    @staticmethod
    def _page_stmt(cursor: int | None, size: int) -> Select[Any]:
        """§4.3 — OFFSET 을 쓰지 않는다. 커서는 마지막으로 본 id 다.

        `size + 1` 을 읽어서 `has_next` 를 판정한다. `COUNT(*)` 는 돌리지 않는다.
        """
        stmt = select_alive(User).order_by(user_table.c.id.desc()).limit(size + 1)
        if cursor is not None:
            stmt = stmt.where(user_table.c.id < cursor)
        return stmt

    @classmethod
    async def list_page(cls, db: AsyncConnection, *, cursor: int | None, size: int) -> list[User]:
        result = await db.execute(cls._page_stmt(cursor, size))
        return all_of(User, result)

    @classmethod
    async def insert(
        cls,
        db: AsyncConnection,
        *,
        username: str,
        email: str,
        nickname: str,
        password_hash: str,
        status: UserStatus = UserStatus.active,
        is_superuser: bool = False,
    ) -> User:
        """행을 넣고 다시 읽어서 돌려준다. 커밋은 하지 않는다 — `TxDep` 가 한다.

        `created_at` 같은 기본값은 컬럼 정의가 채운다 (`common/db/schema.py`).
        """
        result = await db.execute(
            sa.insert(user_table).values(
                username=username,
                email=email,
                nickname=nickname,
                password_hash=password_hash,
                status=status,
                is_superuser=is_superuser,
            )
        )
        pk = result.inserted_primary_key[0]
        row = await cls.get(db, pk)
        if row is None:  # pragma: no cover - 방금 넣은 행이 같은 트랜잭션에서 사라질 수는 없다
            raise RuntimeError('방금 삽입한 행을 다시 읽지 못했다')
        return row

    @classmethod
    async def update(cls, db: AsyncConnection, *, pk: int, changes: dict[str, Any]) -> User | None:
        """부분 수정. 바뀐 행을 돌려준다. 대상이 없으면 None.

        `updated_at` 은 컬럼의 `onupdate` 가 채운다 — SET 절에 손으로 넣지 않는다.
        """
        unknown = sorted(set(changes) - UPDATABLE)
        if unknown:
            raise ValueError(f'수정할 수 없는 컬럼이다: {unknown}')

        if changes:
            await db.execute(sa.update(user_table).where(user_table.c.id == pk, alive(User)).values(**changes))
        return await cls.get(db, pk)

    @staticmethod
    async def mark_deleted(db: AsyncConnection, pk: int) -> int:
        """§1.4 — `deleted = id`. 몇 행이 지워졌는지 돌려준다."""
        result = await db.execute(soft_delete(User, user_table.c.id == pk))
        return result.rowcount


user_repository = UserRepository()
