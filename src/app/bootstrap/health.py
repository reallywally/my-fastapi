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
    checks = {
        'database': await _check_database(request),
        'redis': await _check_redis(request),
    }
    ok = all(checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {'status': 'ok' if ok else 'degraded', 'checks': checks}


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
