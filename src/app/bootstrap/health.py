"""헬스체크. 업무 로직이 아니라 배포 인프라용 엔드포인트라 bootstrap 에 둔다.

- `/health`  — liveness. 자원을 건드리지 않는다. 프로세스가 살아 있으면 200
- `/health/ready` — readiness. DB·Redis 를 실제로 찌른다. 하나라도 실패하면 503

둘을 나누는 이유: liveness 가 DB 를 검사하면 DB 순단에 오케스트레이터가
멀쩡한 프로세스를 죽여버린다.
"""

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(tags=['health'])


@router.get('/health', summary='liveness')
async def health() -> dict[str, str]:
    return {'status': 'ok'}


@router.get('/health/ready', summary='readiness')
async def ready(request: Request, response: Response) -> dict[str, object]:
    checks: dict[str, bool] = {
        'database': await _check_database(request),
        'redis': await _check_redis(request),
    }
    upstreams = await _check_upstreams(request)

    # **업스트림 실패로 503 을 내지 않는다.** 남의 서버가 죽었다고 우리를 로드밸런서에서
    # 빼면 장애가 전파된다. 우리가 여전히 요청을 처리할 수 있는지와, 연동이 건강한지는
    # 다른 질문이다. 업스트림 상태는 보고만 하고 판정에는 넣지 않는다 (§5).
    ok = all(checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    body: dict[str, object] = {'status': 'ok' if ok else 'degraded', 'checks': checks}
    if upstreams:
        body['upstreams'] = upstreams
    return body


async def _check_database(request: Request) -> bool:
    engine = getattr(request.app.state, 'engine', None)
    if engine is None:
        return False
    try:
        async with engine.connect() as connection:
            await connection.execute(text('SELECT 1'))
    except Exception:
        logger.exception('database readiness check failed')
        return False
    return True


async def _check_upstreams(request: Request) -> dict[str, bool]:
    """`health_path` 를 준 업스트림만 찔러본다.

    모든 업스트림을 매 프로브마다 호출하면 우리 readiness 가 남의 서버에 부하를 만든다.
    검사 대상은 설정으로 고른다.
    """
    registry = getattr(request.app.state, 'upstreams', None)
    if registry is None:
        return {}

    results: dict[str, bool] = {}
    for name in registry.names():
        client = registry.get(name)
        if client.config.health_path is None:
            continue
        try:
            await client.request('GET', client.config.health_path, idempotent=False)
        except Exception:
            logger.warning('upstream %s readiness check failed', name)
            results[name] = False
        else:
            results[name] = True
    return results


async def _check_redis(request: Request) -> bool:
    redis = getattr(request.app.state, 'redis', None)
    if redis is None:
        return False
    try:
        await redis.ping()
    except Exception:
        logger.exception('redis readiness check failed')
        return False
    return True
