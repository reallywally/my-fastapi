"""조회수 버퍼의 순수 로직 (§4.5).

DB 는 없다. Redis 는 `fakeredis` 다 — 키 구조와 중복 판정, 그리고 **Redis 가 죽었을 때
조회가 살아남는지**가 여기서 검증된다.
"""

import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.modules.board.post.view_counter import DEDUP_TTL_SECONDS, PENDING_KEY, VIEWED_PREFIX, post_views


@pytest.fixture
async def redis():
    client = FakeAsyncRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


async def test_first_hit_is_counted(redis):
    counted = await post_views.hit(redis, 7, 'viewer-a')

    assert counted is True
    assert await redis.hget(PENDING_KEY, '7') == '1'


async def test_same_viewer_is_counted_once(redis):
    """중복 조회는 세지 않는다 — 새로고침 한 번에 조회수가 오르면 그건 카운터가 아니다."""
    await post_views.hit(redis, 7, 'viewer-a')
    counted_again = await post_views.hit(redis, 7, 'viewer-a')

    assert counted_again is False
    assert await redis.hget(PENDING_KEY, '7') == '1'


async def test_different_viewers_are_counted_separately(redis):
    await post_views.hit(redis, 7, 'viewer-a')
    await post_views.hit(redis, 7, 'viewer-b')

    assert await redis.hget(PENDING_KEY, '7') == '2'


async def test_same_viewer_on_another_post_is_counted(redis):
    await post_views.hit(redis, 7, 'viewer-a')
    await post_views.hit(redis, 8, 'viewer-a')

    assert await redis.hget(PENDING_KEY, '8') == '1'


async def test_dedup_key_expires(redis):
    """TTL 이 없으면 한 번 본 글은 영원히 다시 세지 않는다."""
    await post_views.hit(redis, 7, 'viewer-a')

    ttl = await redis.ttl(f'{VIEWED_PREFIX}:7:viewer-a')

    assert 0 < ttl <= DEDUP_TTL_SECONDS


async def test_redis_failure_does_not_raise():
    """§4.5 — **Redis 가 죽어도 조회는 성공해야 한다.** 카운팅 실패는 삼킨다.

    삼키는 자리가 `hit()` 안이라는 것이 요점이다. 호출자에게 맡기면 언젠가 잊는다.
    """

    class DeadRedis:
        async def set(self, *args, **kwargs):
            raise RedisConnectionError('redis is down')

    counted = await post_views.hit(DeadRedis(), 7, 'viewer-a')

    assert counted is False
