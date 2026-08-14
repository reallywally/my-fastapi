"""lifespan 이 자원과 주기 작업을 함께 관리하는지 (§2.1, §4.5).

다른 테스트는 lifespan 을 돌리지 않는다 — `app.state` 를 직접 채우는 것이 §2.1 이 준
자유다 (`conftest.py`). 그래서 lifespan 자체는 아무 테스트도 통과하지 않는 코드가 되기
쉽고, 실제로 기동에서만 드러나는 실수가 여기 산다.

여기서 확인하는 것은 순서다. **자원이 준비된 뒤에 작업이 뜨고, 자원을 닫기 전에
작업이 멈춘다.** 반대면 돌던 작업이 닫힌 연결을 잡는다.
"""

import asyncio

import pytest
from fakeredis import FakeAsyncRedis

from app.bootstrap.app import create_app
from app.bootstrap.lifespan import lifespan

pytestmark = pytest.mark.asyncio(loop_scope='session')

JOB_NAMES = {'view-flush', 'comment-count-reconcile', 'attachment-cleanup'}


def _running_job_names() -> set[str]:
    return {task.get_name() for task in asyncio.all_tasks()} & JOB_NAMES


async def test_jobs_live_exactly_as_long_as_the_resources(settings, monkeypatch):
    """진짜 Redis 없이 돌린다 — lifespan 이 만드는 클라이언트만 바꿔 끼운다."""
    monkeypatch.setattr('app.bootstrap.lifespan.Redis', FakeAsyncRedis)
    app = create_app()

    async with lifespan(app):
        assert app.state.engine is not None
        assert app.state.storage is not None
        assert _running_job_names() == JOB_NAMES

    assert not _running_job_names()
