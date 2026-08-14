"""`comment_count` 드리프트 보정 (§4.4).

비정규화한 값은 **언젠가 어긋난다고 전제한다.** 트랜잭션 밖에서 행을 고치는 배치,
복구된 데이터, 아직 못 찾은 버그 — 어느 쪽이든 세는 것과 보이는 것이 갈라진다.
그때 사용자가 먼저 알아챈다.

그래서 야간에 실제 카운트와 대조하고 맞춘다. **이 배치 자체가 테스트 대상이다.**

각 슬라이스가 자기 테이블만 만진다 (§4.1):

- 살아 있는 댓글을 세는 것은 `comment` 다
- `post.comment_count` 를 쓰는 것은 `post` 다 (`set_comment_count`)
- 둘을 대조하는 것이 여기이고, `comment` → `post` 방향이라 계약을 어기지 않는다

**한 번에 다 하지 않는다.** id 순으로 페이지를 끊어 훑는다 (§4.3 과 같은 이유) —
SQLite 는 쓰기가 하나뿐이라(§1.6) 긴 트랜잭션 하나가 서버 전체를 세운다.
"""

import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.board.comment.repository import comment_repository
from app.modules.board.post.repository import post_repository

logger = logging.getLogger(__name__)

#: 한 번에 대조하는 글 수.
DEFAULT_BATCH_SIZE: Final = 500


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """훑은 글 수와 고친 글 수. 로그와 테스트가 같은 값을 본다."""

    scanned: int
    repaired: int


async def reconcile_comment_counts(
    db: AsyncConnection, *, batch_size: int = DEFAULT_BATCH_SIZE, limit: int | None = None
) -> ReconcileResult:
    """`post.comment_count` 를 실제 댓글 수로 맞춘다.

    `limit` 은 한 번의 실행에서 훑을 글 수의 상한이다. 게시글이 수백만 개가 되면
    한 번에 다 도는 것이 곧 밤새 도는 것이 된다 — 나눠서 여러 밤에 걸쳐 돈다.

    `commit()` 하지 않는다 (§1.1). 트랜잭션은 호출자의 몫이다.
    """
    scanned = 0
    repaired = 0
    after_id = 0

    while True:
        remaining = batch_size if limit is None else min(batch_size, limit - scanned)
        if remaining <= 0:
            break

        rows = await post_repository.list_counts(db, after_id=after_id, limit=remaining)
        if not rows:
            break

        actual = await comment_repository.count_alive_by_post(db, [post_id for post_id, _ in rows])
        for post_id, stored in rows:
            correct = actual.get(post_id, 0)
            if stored != correct:
                # 묘비(`is_removed`)도 한 개로 센다 (§4.7). 화면에 자리가 남아 있으면
                # 그것은 여전히 댓글이다 — `count_alive_by_post` 가 같은 기준이다.
                await post_repository.set_comment_count(db, post_id, correct)
                logger.info('comment_count 보정: post=%s %s → %s', post_id, stored, correct)
                repaired += 1

        scanned += len(rows)
        after_id = rows[-1][0]

    return ReconcileResult(scanned=scanned, repaired=repaired)
