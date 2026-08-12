"""Redis 의존성.

`db` 를 받듯 `redis` 도 인자로 받는다. 서비스는 stateless 를 유지하고 자원만 주입된다 (§2.1).
"""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis


def get_redis(request: Request) -> Redis:
    client = getattr(request.app.state, 'redis', None)
    if client is None:
        raise RuntimeError('redis 가 app.state 에 없다 — lifespan 이 실행되지 않았다')
    return client


RedisDep = Annotated[Redis, Depends(get_redis)]
