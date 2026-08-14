"""조회수 버퍼 (§4.5).

글을 볼 때마다 `UPDATE post SET view_count = view_count + 1` 을 하면 두 가지가 깨진다.

- 인기 글 **한 행에 UPDATE 가 몰린다.** SQLite 는 쓰기가 DB 전체에 하나뿐이라(§1.6)
  이게 서버 전체를 막는다 — 다른 방언이면 선택이지만 여기서는 필수다
- **읽기 요청이 쓰기 트랜잭션이 된다.** §1.1 에서 `ConnDep` / `TxDep` 을 나눈 의미가
  사라진다

그래서 Redis 에 누적하고 주기적으로 반영한다. 상세 조회 엔드포인트는 `ConnDep`
(끝나면 롤백) 을 그대로 유지한다.

**키 구조는 이 파일이 독점한다** (§2.5 의 `UserSessionStore` 와 같은 이유). 접두사가
밖으로 새면 무효화·집계 코드가 여러 곳에 흩어지고, 하나만 빠뜨렸을 때 조용히 틀린다.

**Redis 가 죽어도 조회는 성공해야 한다.** 그래서 예외를 삼키는 자리가 `hit()` 안이다 —
호출자가 `try` 를 잊을 수 있는 곳에 두면 언젠가 잊는다. 조회수는 요청을 실패시킬
만한 값이 아니다.
"""

import logging
from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.board.post.repository import post_repository

logger = logging.getLogger(__name__)

#: 아직 DB 에 반영되지 않은 증분. `{post_id: 조회수}` 해시 하나다.
PENDING_KEY: Final = 'post:views:pending'

#: 중복 조회 판정 키의 접두사. `post:viewed:{post_id}:{viewer_key}`.
VIEWED_PREFIX: Final = 'post:viewed'

#: 같은 뷰어의 같은 글은 이 시간 동안 한 번만 센다.
DEDUP_TTL_SECONDS: Final = 600


class PostViewCounter:
    @staticmethod
    async def hit(redis: Redis, post_id: int, viewer_key: str) -> bool:
        """조회 1회를 누적한다. 실제로 셌으면 True.

        중복 조회는 세지 않는다 (viewer 기준 10분). `SET ... NX EX` 한 번이 판정과
        기록을 동시에 한다 — 읽고 나서 쓰면 그 사이에 같은 뷰어의 요청이 끼어든다.
        """
        try:
            first_view = await redis.set(f'{VIEWED_PREFIX}:{post_id}:{viewer_key}', 1, ex=DEDUP_TTL_SECONDS, nx=True)
            if not first_view:
                return False
            await redis.hincrby(PENDING_KEY, str(post_id), 1)
        except RedisError:
            # 카운팅 실패는 삼키고 로그만 남긴다 (§4.5). 조회 자체는 성공해야 한다.
            logger.warning('조회수 누적에 실패했다 (post_id=%s)', post_id, exc_info=True)
            return False
        return True

    @staticmethod
    async def flush(db: AsyncConnection, redis: Redis) -> int:
        """pending 해시를 **원자적으로 비우고** DB 에 일괄 반영한다. 반영한 글 수를 돌려준다.

        `HGETALL` 과 `DEL` 사이에 들어온 증분은 유실된다 — 그래서 MULTI/EXEC 로 묶는다.

        DB 반영이 실패하면 **꺼낸 값을 Redis 로 되돌린다.** 되돌리지 않으면 이미 비운
        해시와 롤백된 트랜잭션 사이에서 조회수가 통째로 사라진다. 되돌리기까지
        실패하면 그때는 로그만 남긴다 — 그 이상은 할 수 있는 게 없다.

        `commit()` 하지 않는다 (§1.1). 트랜잭션은 호출자(`bootstrap/jobs.py`)의 몫이다.
        """
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hgetall(PENDING_KEY)
            pipe.delete(PENDING_KEY)
            pending, _ = await pipe.execute()

        if not pending:
            return 0

        counts = {int(post_id): int(delta) for post_id, delta in pending.items()}
        try:
            for post_id, delta in counts.items():
                await post_repository.bump_view_count(db, post_id, delta)
        except Exception:
            await PostViewCounter._restore(redis, counts)
            raise
        return len(counts)

    @staticmethod
    async def _restore(redis: Redis, counts: dict[int, int]) -> None:
        try:
            async with redis.pipeline(transaction=True) as pipe:
                for post_id, delta in counts.items():
                    pipe.hincrby(PENDING_KEY, str(post_id), delta)
                await pipe.execute()
        except RedisError:  # pragma: no cover - Redis 와 DB 가 동시에 죽은 경우
            logger.exception('조회수 %d건을 되돌리지 못했다', len(counts))


post_views = PostViewCounter()
