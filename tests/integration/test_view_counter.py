"""flush 가 실제 DB 에 반영되는지 (§4.5).

Redis 는 `fakeredis`, DB 는 진짜 SQLite 다. 검증 대상은 두 가지 —
**증분이 DB 에서 더해지는가**(규칙 #13), 그리고 **실패했을 때 값이 사라지지 않는가**.
"""

import pytest
from fakeredis import FakeAsyncRedis

from app.modules.board.post.repository import post_repository
from app.modules.board.post.view_counter import PENDING_KEY, post_views
from tests.factories import create_board, create_post, create_user


@pytest.fixture
async def redis():
    client = FakeAsyncRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def post(db):
    user = await create_user(db)
    board = await create_board(db)
    return await create_post(db, board_id=board.id, author_id=user.id)


async def test_flush_applies_pending_counts(db, redis, post):
    await post_views.hit(redis, post.id, 'viewer-a')
    await post_views.hit(redis, post.id, 'viewer-b')

    applied = await post_views.flush(db, redis)

    assert applied == 1
    assert (await post_repository.get(db, post.id)).view_count == 2


async def test_flush_empties_the_buffer(db, redis, post):
    """두 번 반영하면 조회수가 두 배가 된다. 비우는 것이 flush 의 절반이다."""
    await post_views.hit(redis, post.id, 'viewer-a')
    await post_views.flush(db, redis)

    assert await post_views.flush(db, redis) == 0
    assert (await post_repository.get(db, post.id)).view_count == 1
    assert await redis.hgetall(PENDING_KEY) == {}


async def test_flush_on_empty_buffer_does_nothing(db, redis):
    assert await post_views.flush(db, redis) == 0


async def test_flush_adds_to_the_existing_value(db, redis, post):
    """규칙 #13 — 앱에서 읽고 더해서 쓰지 않는다. 이미 있던 값 위에 더해져야 한다."""
    await post_repository.bump_view_count(db, post.id, 100)
    await post_views.hit(redis, post.id, 'viewer-a')

    await post_views.flush(db, redis)

    assert (await post_repository.get(db, post.id)).view_count == 101


async def test_counts_return_to_redis_when_the_database_write_fails(db, redis, post):
    """DB 반영이 실패하면 꺼낸 값을 되돌린다.

    되돌리지 않으면 비워진 해시와 롤백된 트랜잭션 사이에서 조회수가 통째로 사라진다.
    """
    await post_views.hit(redis, post.id, 'viewer-a')

    class BrokenConnection:
        async def execute(self, *args, **kwargs):
            raise RuntimeError('database is gone')

    with pytest.raises(RuntimeError):
        await post_views.flush(BrokenConnection(), redis)

    assert await redis.hget(PENDING_KEY, str(post.id)) == '1'


async def test_deleted_post_does_not_come_back(db, redis, post):
    """삭제된 글의 조회수는 반영하지 않는다 — `alive()` 가 걸려 있다 (§2.4)."""
    await post_views.hit(redis, post.id, 'viewer-a')
    await post_repository.mark_deleted(db, post.id)

    await post_views.flush(db, redis)

    assert await post_repository.get(db, post.id) is None
