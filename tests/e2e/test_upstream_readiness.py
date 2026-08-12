"""`/health/ready` 의 업스트림 보고 (§5).

핵심 판단: **업스트림이 죽어도 우리는 503 이 아니다.** 남의 서버가 죽었다고 우리를
로드밸런서에서 빼면 장애가 전파된다. "우리가 요청을 처리할 수 있는가" 와 "연동이
건강한가" 는 다른 질문이고, readiness 는 앞의 질문에 답한다.
"""

import httpx
import pytest

from app.common.http.client import UpstreamClient
from app.common.http.registry import UpstreamRegistry
from app.core.upstream import UpstreamConfig

pytestmark = pytest.mark.asyncio(loop_scope='session')

READY = '/health/ready'


def _upstream(name: str, handler, *, health_path: str | None = '/healthz') -> UpstreamClient:
    config = UpstreamConfig(
        base_url=f'https://{name}.example.com',
        health_path=health_path,
        max_retries=0,
        retry_backoff_seconds=0.001,
    )
    http_client = httpx.AsyncClient(base_url=str(config.base_url), transport=httpx.MockTransport(handler))
    return UpstreamClient(name, config, http_client)


@pytest.fixture
def with_upstreams(app):
    """app.state 를 갈아끼운다 — §2.1 이 예고한 fake 주입 지점."""
    original = getattr(app.state, 'upstreams', None)

    def _install(registry: UpstreamRegistry):
        app.state.upstreams = registry
        return registry

    yield _install
    if original is None:
        del app.state.upstreams
    else:
        app.state.upstreams = original


async def test_healthy_upstreams_are_reported(client, with_upstreams):
    with_upstreams(
        UpstreamRegistry(
            {
                'a': _upstream('a', lambda request: httpx.Response(200)),
                'b': _upstream('b', lambda request: httpx.Response(204)),
            }
        )
    )

    response = await client.get(READY)

    assert response.status_code == 200
    assert response.json()['upstreams'] == {'a': True, 'b': True}


async def test_a_dead_upstream_is_reported_but_does_not_make_us_unready(client, with_upstreams):
    with_upstreams(
        UpstreamRegistry(
            {
                'a': _upstream('a', lambda request: httpx.Response(200)),
                'b': _upstream('b', lambda request: httpx.Response(503)),
            }
        )
    )

    response = await client.get(READY)

    assert response.status_code == 200  # ← 503 이 아니다. 장애를 전파하지 않는다
    assert response.json()['status'] == 'ok'
    assert response.json()['upstreams'] == {'a': True, 'b': False}


async def test_a_timing_out_upstream_is_reported_as_false(client, with_upstreams):
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout('slow')

    with_upstreams(UpstreamRegistry({'a': _upstream('a', _timeout)}))

    assert (await client.get(READY)).json()['upstreams'] == {'a': False}


async def test_upstreams_without_a_health_path_are_not_probed(client, with_upstreams):
    """모든 업스트림을 매 프로브마다 찌르면 우리 readiness 가 남의 서버에 부하를 만든다."""
    calls: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with_upstreams(UpstreamRegistry({'a': _upstream('a', _record, health_path=None)}))

    body = (await client.get(READY)).json()

    assert 'upstreams' not in body
    assert calls == []


async def test_the_key_is_absent_when_no_upstreams_are_configured(client, with_upstreams):
    with_upstreams(UpstreamRegistry({}))

    body = (await client.get(READY)).json()

    assert 'upstreams' not in body
    assert body['status'] == 'ok'
