"""고아 첨부 정리 (§4.9).

행과 파일이 다른 곳에 살면 어긋나는 경우가 생긴다. 여기서 청소하는 것은 두 가지다.

1. **글에 붙지 않은 행.** 업로드는 됐는데 글 저장이 실패한 자국이다. TTL 이 지난
   것만 본다 — 방금 올라온 미연결 파일은 고아가 아니라 **진행 중**이다
2. **아무도 모르는 파일.** 저장은 됐는데 행 삽입이 실패했다. 업로드는 "저장 → 삽입"
   순서고 (§4.9) 파일 쓰기는 롤백되지 않아서, 트랜잭션이 뒤에서 실패하면 파일만 남는다

**두 단계를 나눈 것은 실수를 되돌릴 수 없기 때문이다.** 행 삭제는 롤백되지만 파일
삭제는 안 된다. 한 함수에서 둘 다 하면, 트랜잭션이 뒤에서 실패했을 때 행은 살아나고
파일만 사라진다 — 다운로드가 500 을 내는 행이 남는다.

그래서 1단계는 행만 지우고(쓰기 트랜잭션), 2단계는 **커밋된 사실만 보고** 파일을
지운다(읽기 트랜잭션). 1단계가 지운 행의 파일은 같은 실행의 2단계에서, 혹은 다음
실행에서 정리된다 — 늦게 지우는 것은 문제가 아니지만 잘못 지우는 것은 문제다.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.storage import Storage
from app.modules.board.attachment.repository import attachment_repository

logger = logging.getLogger(__name__)

#: 한 번에 정리하는 행 수.
DEFAULT_BATCH_SIZE: Final = 200


def _cutoff(older_than: timedelta, now: datetime | None) -> datetime:
    """`now` 를 인자로 받는 이유는 테스트다.

    안에서 `datetime.now()` 를 부르면 "하루 전"을 만들려고 테스트가 시계를 조작하거나
    행의 `created_at` 을 손으로 고쳐야 한다.
    """
    return (now or datetime.now(UTC)) - older_than


async def sweep_unattached_rows(
    db: AsyncConnection,
    *,
    older_than: timedelta,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """1단계 — 글에 붙지 않은 채 TTL 이 지난 행을 지운다. 지운 행 수를 돌려준다.

    파일은 손대지 않는다. `commit()` 도 하지 않는다 (§1.1).
    """
    rows = await attachment_repository.list_unattached(db, before=_cutoff(older_than, now), limit=batch_size)
    for row in rows:
        await attachment_repository.mark_deleted(db, row.id)
        logger.info('미연결 첨부 삭제: id=%s key=%s', row.id, row.storage_key)
    return len(rows)


async def sweep_unknown_files(
    db: AsyncConnection,
    storage: Storage,
    *,
    older_than: timedelta,
    now: datetime | None = None,
) -> int:
    """2단계 — 어떤 행도 보호하지 않는 파일을 지운다. 지운 파일 수를 돌려준다.

    **DB 에는 쓰지 않는다.** 읽은 사실은 전부 커밋된 상태다 — 그래서 이 단계의 삭제는
    롤백될 트랜잭션과 엮이지 않는다.

    `mtime` 을 보는 이유: 방금 저장된 파일은 아직 행이 없는 것이 정상이다. 같은 요청이
    아직 진행 중일 수 있고, 지우면 우리가 그 업로드를 깨뜨린다.
    """
    cutoff = _cutoff(older_than, now).timestamp()
    protected = await attachment_repository.protected_keys(db)

    deleted = 0
    for key in await storage.keys():
        if key in protected:
            continue
        modified_at = await storage.modified_at(key)
        if modified_at is None or modified_at >= cutoff:
            continue
        if await storage.delete(key):
            deleted += 1
            logger.info('고아 파일 삭제: %s', key)
    return deleted
