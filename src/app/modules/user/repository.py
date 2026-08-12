"""쿼리만. 업무 규칙은 없다 (§1.2).

두 가지를 절대 하지 않는다:
- **`commit()`** — 트랜잭션은 엔드포인트가 결정한다 (§1.1, 규칙 #1). `flush()` 만 쓴다.
- **`deleted == 0`** — 전역 ORM 필터가 붙인다 (§2.4, 규칙 #6). 여기 쓰면 중복이다.

`crud` 가 아니라 `repository` 인 이유: CRUD 5개가 아니라 이 모듈의 모든 쿼리가 여기 산다.
"""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db import soft_delete
from app.modules.user.model import User


class UserRepository:
    @staticmethod
    async def get(db: AsyncSession, pk: int) -> User | None:
        return await db.get(User, pk)

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> User | None:
        return (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> User | None:
        return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    @staticmethod
    def _page_stmt(cursor: int | None, size: int) -> Select[tuple[User]]:
        """§4.3 — OFFSET 을 쓰지 않는다. 커서는 마지막으로 본 id 다.

        `size + 1` 을 읽어서 `has_next` 를 판정한다. `COUNT(*)` 는 돌리지 않는다.
        """
        stmt = select(User).order_by(User.id.desc()).limit(size + 1)
        if cursor is not None:
            stmt = stmt.where(User.id < cursor)
        return stmt

    @classmethod
    async def list_page(cls, db: AsyncSession, *, cursor: int | None, size: int) -> list[User]:
        rows = await db.execute(cls._page_stmt(cursor, size))
        return list(rows.scalars().all())

    @staticmethod
    async def insert(db: AsyncSession, user: User) -> User:
        db.add(user)
        # flush 까지만. 커밋은 TxDep 가 한다. id 는 여기서 채워진다.
        await db.flush()
        return user

    @staticmethod
    async def mark_deleted(db: AsyncSession, pk: int) -> int:
        """§1.4 — `deleted = id`. 몇 행이 지워졌는지 돌려준다."""
        result = await db.execute(soft_delete(User, User.id == pk))
        return result.rowcount


user_repository = UserRepository()
