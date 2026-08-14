"""조회수가 요청 경로에서 어떻게 도는지 (§4.5).

두 가지를 못 박는다:

- 상세 조회는 **읽기 트랜잭션**이다. 증분은 Redis 로 가고 DB 는 flush 때만 바뀐다
- **Redis 가 죽어도 조회는 200 이다.** 조회수는 요청을 실패시킬 만한 값이 아니다
"""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.modules.board.post.view_counter import PENDING_KEY
from tests.factories import create_board, create_post, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')

POSTS = '/api/v1/posts'


@pytest.fixture
async def redis(app):
    """앱이 실제로 쓰는 Redis. **테스트마다 통째로 비운다.**

    DB 는 트랜잭션 롤백으로 격리되지만 (§2.8) Redis 는 롤백되지 않는다. 게다가
    롤백하면 id 시퀀스도 되돌아가서 다음 테스트의 글이 **같은 id** 를 받는다 —
    비우지 않으면 앞 테스트가 남긴 중복 판정 키가 다음 조회를 조용히 무시한다.
    """
    await app.state.redis.flushdb()
    return app.state.redis


async def _post(db):
    board = await create_board(db)
    user = await create_user(db)
    return await create_post(db, board_id=board.id, author_id=user.id)


async def test_reading_a_post_buffers_the_view_in_redis(client, db, redis):
    post = await _post(db)

    response = await client.get(f'{POSTS}/{post.id}')

    assert response.status_code == 200
    assert await redis.hget(PENDING_KEY, str(post.id)) == '1'


async def test_the_response_shows_the_stored_count_not_the_buffer(client, db, redis):
    """pending 을 합산해 정확하게 보이려는 유혹을 참는다 (§4.5).

    합산하면 모든 조회가 Redis 왕복을 한 번 더 하고, 그러고도 값은 근사치다.
    """
    post = await _post(db)

    body = (await client.get(f'{POSTS}/{post.id}')).json()

    assert body['view_count'] == 0
    assert await redis.hget(PENDING_KEY, str(post.id)) == '1'


async def test_the_same_viewer_is_counted_once(client, db, redis):
    """같은 클라이언트가 새로고침해도 한 번이다 — e2e 에서는 IP·UA 가 같다."""
    post = await _post(db)

    for _ in range(5):
        await client.get(f'{POSTS}/{post.id}')

    assert await redis.hget(PENDING_KEY, str(post.id)) == '1'


async def test_an_unreadable_post_is_not_counted(client, db, redis):
    """볼 수 없는 글의 조회수를 올리면 비공개 게시판의 존재가 조회수로 새어 나간다."""
    board = await create_board(db, read_role='member')
    user = await create_user(db)
    post = await create_post(db, board_id=board.id, author_id=user.id)

    response = await client.get(f'{POSTS}/{post.id}')

    assert response.status_code == 401
    assert await redis.hget(PENDING_KEY, str(post.id)) is None


async def test_reading_survives_a_dead_redis(client, db, app):
    """§4.5 — 카운팅 실패는 삼키고 로그만 남긴다."""
    post = await _post(db)

    class DeadRedis:
        async def set(self, *args, **kwargs):
            raise RedisConnectionError('redis is down')

    original = app.state.redis
    app.state.redis = DeadRedis()
    try:
        response = await client.get(f'{POSTS}/{post.id}')
    finally:
        app.state.redis = original

    assert response.status_code == 200
    assert response.json()['id'] == post.id
