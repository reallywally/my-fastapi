"""라우터를 관통하는 E2E. sync TestClient 가 아니라 httpx.AsyncClient 다 (§2.8)."""

import pytest

from app.core.constants import REQUEST_ID_HEADER

pytestmark = pytest.mark.asyncio(loop_scope='session')


async def test_liveness_does_not_touch_resources(client):
    response = await client.get('/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


async def test_readiness_checks_database_and_redis(client):
    response = await client.get('/health/ready')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'checks': {'database': True, 'redis': True}}


async def test_readiness_reports_503_when_redis_is_down(client, app):
    """자원이 죽었을 때 200 을 주면 오케스트레이터가 트래픽을 계속 보낸다."""
    original = app.state.redis
    app.state.redis = None
    try:
        response = await client.get('/health/ready')
    finally:
        app.state.redis = original

    assert response.status_code == 503
    assert response.json()['checks'] == {'database': True, 'redis': False}


async def test_every_response_carries_a_request_id(client):
    response = await client.get('/health')

    assert response.headers[REQUEST_ID_HEADER]
