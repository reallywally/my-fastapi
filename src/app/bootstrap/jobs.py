"""주기 작업 (§4.4, §4.5, §4.9). **조립만 한다** — 업무 로직은 각 슬라이스에 있다.

여기가 하는 일은 셋뿐이다: 언제 돌릴지, 어떤 트랜잭션으로 돌릴지, 실패하면 어떻게 할지.

**실패해도 루프는 죽지 않는다.** 한 번의 flush 가 실패했다고 소비자가 멈추면 그 뒤로
조회수가 영원히 반영되지 않고, 아무도 모른다. 예외는 로그로 남기고 다음 주기를 기다린다.

**여러 워커가 같이 돌아도 안전해야 한다.** 프로세스마다 이 루프가 돈다:

- 조회수 flush 는 Redis 에서 **원자적으로 꺼내므로** 두 워커가 같은 증분을 두 번
  반영하지 않는다 (`view_counter.flush`)
- 보정 배치와 고아 정리는 **멱등**하다. 같은 결과를 두 번 계산해서 두 번 써도 값이 같다

주기를 0 으로 두면 그 작업은 아예 돌지 않는다. 배치를 별도 프로세스(k8s CronJob)로
빼는 배포에서 쓰라고 남긴 스위치다 — 그때 `api` 워커는 전부 0 이면 된다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.db import write_transaction
from app.common.storage import Storage
from app.core.config import Settings
from app.modules.board.attachment.maintenance import sweep_unattached_rows, sweep_unknown_files
from app.modules.board.comment.maintenance import reconcile_comment_counts
from app.modules.board.post.view_counter import post_views

logger = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


async def run_periodically(name: str, interval_seconds: float, job: Job) -> None:
    """`interval_seconds` 마다 `job` 을 부른다. 취소될 때까지.

    **첫 실행 전에 한 주기를 기다린다.** 기동 직후는 마이그레이션·워밍업과 겹치는
    시간이고, 워커 여럿이 동시에 뜨면 배치도 동시에 뜬다.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            # 한 번의 실패로 루프를 끝내지 않는다. 끝나면 아무도 모른 채 멈춘다.
            logger.exception('주기 작업 실패: %s', name)


async def flush_view_counts(engine: AsyncEngine, redis: Redis) -> None:
    """§4.5 — Redis 에 모인 조회수를 DB 에 반영한다.

    조회 경로가 아니라 여기서만 `view_count` 를 쓴다. 그래서 상세 조회가 `ConnDep`
    으로 남을 수 있다 (규칙 #12).
    """
    async with write_transaction(engine.connect) as db:
        applied = await post_views.flush(db, redis)
    if applied:
        logger.info('조회수 반영: %d개 글', applied)


async def reconcile_counts(engine: AsyncEngine) -> None:
    """§4.4 — 비정규화한 `comment_count` 를 실제 값과 대조·보정한다."""
    async with write_transaction(engine.connect) as db:
        result = await reconcile_comment_counts(db)
    if result.repaired:
        logger.warning('comment_count 드리프트 보정: %d/%d', result.repaired, result.scanned)


async def cleanup_attachments(engine: AsyncEngine, storage: Storage, ttl: timedelta) -> None:
    """§4.9 — 미연결 행을 지우고(1단계), 커밋된 뒤에 고아 파일을 지운다(2단계).

    **트랜잭션이 두 개인 것이 핵심이다.** 파일 삭제는 롤백되지 않으므로, 행 삭제가
    확정된 뒤에만 파일에 손을 댄다.
    """
    async with write_transaction(engine.connect) as db:
        rows = await sweep_unattached_rows(db, older_than=ttl)

    # 2단계는 읽기만 한다 — 그래서 트랜잭션을 열지 않고 새 연결로 간다.
    # 1단계의 삭제가 이미 커밋된 뒤라야 그 파일을 지울 수 있다.
    async with engine.connect() as db:
        files = await sweep_unknown_files(db, storage, older_than=ttl)

    if rows or files:
        logger.info('첨부 정리: 행 %d개, 파일 %d개', rows, files)


def start_jobs(app: FastAPI, settings: Settings) -> list[asyncio.Task[None]]:
    """설정된 주기 작업을 띄운다. 반환한 태스크는 lifespan 이 정리한다."""
    engine: AsyncEngine = app.state.engine
    redis: Redis = app.state.redis
    storage: Storage = app.state.storage
    ttl = timedelta(seconds=settings.attachment_orphan_ttl_seconds)

    schedule: list[tuple[str, int, Job]] = [
        ('view-flush', settings.view_flush_interval_seconds, lambda: flush_view_counts(engine, redis)),
        (
            'comment-count-reconcile',
            settings.comment_count_reconcile_interval_seconds,
            lambda: reconcile_counts(engine),
        ),
        (
            'attachment-cleanup',
            settings.attachment_cleanup_interval_seconds,
            lambda: cleanup_attachments(engine, storage, ttl),
        ),
    ]

    tasks: list[asyncio.Task[None]] = []
    for name, interval, job in schedule:
        if interval <= 0:
            logger.info('주기 작업 꺼짐: %s', name)
            continue
        tasks.append(asyncio.create_task(run_periodically(name, interval, job), name=name))
    return tasks


async def stop_jobs(tasks: list[asyncio.Task[None]]) -> None:
    """취소하고 **끝날 때까지 기다린다.**

    기다리지 않으면 루프가 절반쯤 돌던 중에 엔진이 dispose 되고, 종료 로그가
    "connection is closed" 로 뒤덮인다.
    """
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
