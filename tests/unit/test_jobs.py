"""주기 작업 러너 (§4.4, §4.5, §4.9).

DB 도 Redis 도 없다. 여기서 보는 것은 **스케줄링의 성질** 하나다:

- 한 번의 실패로 루프가 죽지 않는다. 죽으면 그 뒤로 아무 일도 안 일어나고 아무도 모른다
- 주기를 0 으로 두면 그 작업은 아예 뜨지 않는다 (배치를 별도 프로세스로 뺄 때 쓴다)
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.bootstrap.jobs import run_periodically, start_jobs, stop_jobs
from app.core.config import Settings


async def test_a_failing_job_does_not_kill_the_loop():
    calls = []
    ran_enough = asyncio.Event()

    async def job():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError('첫 실행이 실패했다')
        if len(calls) >= 3:
            ran_enough.set()

    task = asyncio.create_task(run_periodically('test', 0.01, job))
    try:
        await asyncio.wait_for(ran_enough.wait(), timeout=5)
    finally:
        await stop_jobs([task])

    assert len(calls) >= 3


async def test_the_loop_stops_when_cancelled():
    async def job():
        return None

    task = asyncio.create_task(run_periodically('test', 0.01, job))
    await asyncio.sleep(0.02)

    await stop_jobs([task])

    assert task.cancelled()


def _app() -> SimpleNamespace:
    state = SimpleNamespace(engine=object(), redis=object(), storage=object())
    return SimpleNamespace(state=state)


async def test_a_zero_interval_disables_a_job():
    settings = Settings(
        view_flush_interval_seconds=0,
        comment_count_reconcile_interval_seconds=0,
        attachment_cleanup_interval_seconds=0,
    )

    tasks = start_jobs(_app(), settings)

    assert tasks == []


@pytest.mark.parametrize(
    ('field', 'name'),
    [
        ('view_flush_interval_seconds', 'view-flush'),
        ('comment_count_reconcile_interval_seconds', 'comment-count-reconcile'),
        ('attachment_cleanup_interval_seconds', 'attachment-cleanup'),
    ],
)
async def test_each_job_is_scheduled_by_its_own_setting(field, name):
    off = dict.fromkeys(
        (
            'view_flush_interval_seconds',
            'comment_count_reconcile_interval_seconds',
            'attachment_cleanup_interval_seconds',
        ),
        0,
    )
    settings = Settings(**{**off, field: 60})

    tasks = start_jobs(_app(), settings)
    try:
        assert [task.get_name() for task in tasks] == [name]
    finally:
        await stop_jobs(tasks)
