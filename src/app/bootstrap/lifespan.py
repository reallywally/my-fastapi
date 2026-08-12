"""I/O 자원의 유일한 생성 지점 (§2.1).

**모듈 전역 인스턴스는 0개다.** import 만으로는 어떤 연결도 열리지 않는다.
실패하면 예외를 올린다 — `sys.exit()` 을 쓰지 않는다 (§3.3).

여기서 `create_all()` 을 호출하지 않는다. 스키마의 유일한 소스는 마이그레이션이다 (§2.3).
엔진의 SQLite 설정(PRAGMA, 명시적 BEGIN)은 `common/db/engine.py` 가 안다.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.common.db.engine import create_engine
from app.core.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    engine = create_engine(settings)
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)

    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    app.state.redis = redis

    try:
        # 기동 시점에 연결을 확인한다. 실패하면 예외가 올라가 기동이 실패한다.
        async with engine.connect() as connection:
            await connection.execute(text('SELECT 1'))
        await redis.ping()

        logger.info('resources ready (env=%s)', settings.environment)
        yield
    finally:
        # ping 이 실패해도 여기까지 온다 — 열린 자원을 남기지 않는다.
        await redis.aclose()
        await engine.dispose()
        logger.info('resources disposed')
