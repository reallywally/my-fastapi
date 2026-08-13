"""테스트 데이터 팩토리 (§2.8).

테스트마다 사용자 필드를 손으로 채우면, 필드가 하나 추가될 때 모든 테스트를 고쳐야 한다.
기본값은 여기에만 둔다.

삽입은 레포지토리를 거친다. 별도의 INSERT 를 여기 두면 그게 두 번째 진실이 되고,
컬럼이 바뀌었을 때 프로덕션 코드가 아니라 테스트만 조용히 통과하는 상황이 생긴다.
"""

from itertools import count
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.security import hash_password
from app.modules.board.board.model import Board
from app.modules.board.board.repository import board_repository
from app.modules.board.post.model import Post
from app.modules.board.post.repository import post_repository
from app.modules.user.model import User
from app.modules.user.repository import user_repository

_sequence = count(1)

#: 해싱은 비싸다(argon2). 비밀번호를 검증하지 않는 테스트에서 매번 새로 만들 이유가 없다.
DEFAULT_PASSWORD = 'hunter2-long-enough'
_default_hash: str | None = None


def password_hash() -> str:
    global _default_hash  # noqa: PLW0603 — 프로세스 캐시. 테스트 속도에 직접 영향이 있다
    if _default_hash is None:
        _default_hash = hash_password(DEFAULT_PASSWORD)
    return _default_hash


def user_fields(**overrides: Any) -> dict[str, Any]:
    """`user_repository.insert` 에 그대로 넘길 수 있는 필드 묶음."""
    n = next(_sequence)
    return {
        'username': f'user{n}',
        'email': f'user{n}@example.com',
        'nickname': f'사용자{n}',
        'password_hash': password_hash(),
    } | overrides


async def create_user(db: AsyncConnection, **overrides: Any) -> User:
    # commit 하지 않는다 — 테스트 트랜잭션 안에 머문다 (§2.8)
    return await user_repository.insert(db, **user_fields(**overrides))


async def create_users(db: AsyncConnection, count_: int, **overrides: Any) -> list[User]:
    return [await create_user(db, **overrides) for _ in range(count_)]


# ------------------------------------------------------------------ 게시판 (§4)


def board_fields(**overrides: Any) -> dict[str, Any]:
    """`board_repository.insert` 에 그대로 넘길 수 있는 필드 묶음.

    기본값은 **공개 게시판**이다. `read_role='anonymous'` 라야 주체 없이 읽히고,
    그게 Phase 4 에서 실제로 검증 가능한 유일한 경로다 (§4.6).
    """
    n = next(_sequence)
    return {
        'slug': f'board-{n}',
        'name': f'게시판{n}',
        'read_role': 'anonymous',
        'write_role': 'member',
    } | overrides


async def create_board(db: AsyncConnection, **overrides: Any) -> Board:
    return await board_repository.insert(db, **board_fields(**overrides))


def post_fields(**overrides: Any) -> dict[str, Any]:
    n = next(_sequence)
    return {'title': f'글 제목 {n}', 'content': f'본문 {n}'} | overrides


async def create_post(db: AsyncConnection, *, board_id: int, author_id: int, **overrides: Any) -> Post:
    return await post_repository.insert(db, board_id=board_id, author_id=author_id, **post_fields(**overrides))


async def create_posts(db: AsyncConnection, count_: int, *, board_id: int, author_id: int, **overrides: Any):
    return [await create_post(db, board_id=board_id, author_id=author_id, **overrides) for _ in range(count_)]
