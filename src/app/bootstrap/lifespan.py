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
from app.common.http.registry import create_registry
from app.core.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    engine = create_engine(settings)
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    upstreams, http_clients = create_registry(settings.upstreams)

    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    app.state.redis = redis
    app.state.upstreams = upstreams

    try:
        # 기동 시점에 연결을 확인한다. 실패하면 예외가 올라가 기동이 실패한다.
        async with engine.connect() as connection:
            await connection.execute(text('SELECT 1'))
        await redis.ping()

        # 업스트림은 **찔러보지 않는다.** 남의 서버가 잠깐 죽었다고 우리 배포가 막히면
        # 장애가 전파된다. 도달 여부는 `/health/ready` 가 계속 보고한다.
        logger.info('resources ready (env=%s)', settings.environment)
        yield
    finally:
        # ping 이 실패해도 여기까지 온다 — 열린 자원을 남기지 않는다.
        for http_client in http_clients:
            await http_client.aclose()
        await redis.aclose()
        await engine.dispose()
        logger.info('resources disposed')
