"""테스트 데이터 팩토리 (§2.8).

테스트마다 사용자 필드를 손으로 채우면, 필드가 하나 추가될 때 모든 테스트를 고쳐야 한다.
기본값은 여기에만 둔다.
"""

from itertools import count

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security import hash_password
from app.modules.user.model import User, UserStatus

_sequence = count(1)

#: 해싱은 비싸다(argon2). 비밀번호를 검증하지 않는 테스트에서 매번 새로 만들 이유가 없다.
DEFAULT_PASSWORD = 'hunter2-long-enough'
_default_hash: str | None = None


def password_hash() -> str:
    global _default_hash  # noqa: PLW0603 — 프로세스 캐시. 테스트 속도에 직접 영향이 있다
    if _default_hash is None:
        _default_hash = hash_password(DEFAULT_PASSWORD)
    return _default_hash


def build_user(**overrides) -> User:
    n = next(_sequence)
    fields = {
        'username': f'user{n}',
        'email': f'user{n}@example.com',
        'nickname': f'사용자{n}',
        'password_hash': password_hash(),
        'status': UserStatus.active,
        'is_superuser': False,
    } | overrides
    return User(**fields)


async def create_user(db: AsyncSession, **overrides) -> User:
    user = build_user(**overrides)
    db.add(user)
    await db.flush()  # commit 하지 않는다 — 테스트 트랜잭션 안에 머문다 (§2.8)
    return user


async def create_users(db: AsyncSession, count_: int, **overrides) -> list[User]:
    users = [build_user(**overrides) for _ in range(count_)]
    db.add_all(users)
    await db.flush()
    return users
