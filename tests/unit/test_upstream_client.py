"""전송 계층 (§5, `common/http/client.py`).

가짜 업스트림을 `httpx.MockTransport` 로 붙인다 — 실제 소켓이 없으니 DB·네트워크 없이
밀리초 단위로 돈다. 검증 대상은 **실패했을 때 무엇을 하는가** 다.
"""

import asyncio

import httpx
import pytest

from app.common.errors.exceptions import (
    UpstreamPayloadError,
    UpstreamStatusError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from app.common.http.client import UpstreamClient
from app.common.middleware import request_id_ctx
from app.core.constants import REQUEST_ID_HEADER
from app.core.upstream import UpstreamConfig


def _client(handler, **config_overrides) -> UpstreamClient:
    config = UpstreamConfig(
        base_url='https://a.example.com',
        retry_backoff_seconds=0.001,  # 테스트가 실제로 기다리지 않게
        retry_backoff_max_seconds=0.001,
        **config_overrides,
    )
    http_client = httpx.AsyncClient(base_url=str(config.base_url), transport=httpx.MockTransport(handler))
    return UpstreamClient('a', config, http_client)


class _Recorder:
    """호출 횟수와 마지막 요청을 기록하는 가짜 업스트림."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def calls(self) -> int:
        return len(self.requests)


# ------------------------------------------------------------------ 성공 경로


async def test_returns_the_response_on_success():
    handler = _Recorder(httpx.Response(200, json={'ok': True}))

    response = await _client(handler).request('GET', '/thing')

    assert response.status_code == 200
    assert handler.calls == 1


async def test_get_json_decodes_the_body():
    handler = _Recorder(httpx.Response(200, json={'value': 7}))

    assert await _client(handler).get_json('/thing') == {'value': 7}


async def test_api_key_is_attached():
    handler = _Recorder(httpx.Response(200, json={}))

    await _client(handler, api_key='Bearer s3cret').request('GET', '/thing')

    assert handler.requests[0].headers['authorization'] == 'Bearer s3cret'


async def test_request_id_is_propagated():
    """우리 로그와 상대 로그를 이어붙이려면 요청 ID 가 넘어가야 한다 (§0)."""
    handler = _Recorder(httpx.Response(200, json={}))
    token = request_id_ctx.set('abc123')
    try:
        await _client(handler).request('GET', '/thing')
    finally:
        request_id_ctx.reset(token)

    assert handler.requests[0].headers[REQUEST_ID_HEADER] == 'abc123'


async def test_no_request_id_header_when_there_is_no_request():
    """백그라운드 작업에서 호출될 때 빈 헤더를 보내지 않는다."""
    handler = _Recorder(httpx.Response(200, json={}))

    await _client(handler).request('GET', '/thing')

    assert REQUEST_ID_HEADER.lower() not in handler.requests[0].headers


# --------------------------------------------------------------------- 재시도


async def test_retries_a_timeout_on_get():
    handler = _Recorder(httpx.ConnectTimeout('slow'), httpx.Response(200, json={}))

    response = await _client(handler, max_retries=2).request('GET', '/thing')

    assert response.status_code == 200
    assert handler.calls == 2


async def test_does_not_retry_a_post():
    """타임아웃은 '처리되지 않았다' 가 아니라 '결과를 못 봤다' 는 뜻이다.

    POST 를 재시도하면 같은 요청이 두 번 처리될 수 있다.
    """
    handler = _Recorder(httpx.ConnectTimeout('slow'))

    with pytest.raises(UpstreamTimeoutError):
        await _client(handler, max_retries=3).request('POST', '/thing', json={'a': 1})

    assert handler.calls == 1


async def test_a_post_can_opt_in_to_retrying():
    handler = _Recorder(httpx.ConnectTimeout('slow'), httpx.Response(200, json={}))

    response = await _client(handler, max_retries=2).request('POST', '/thing', idempotent=True)

    assert response.status_code == 200
    assert handler.calls == 2


@pytest.mark.parametrize('status_code', [429, 502, 503, 504])
async def test_retries_transient_statuses(status_code: int):
    handler = _Recorder(httpx.Response(status_code), httpx.Response(200, json={}))

    response = await _client(handler, max_retries=2).request('GET', '/thing')

    assert response.status_code == 200
    assert handler.calls == 2


async def test_does_not_retry_500():
    """500 은 보통 상대의 버그다. 재시도는 장애 중인 서버에 부하만 보탠다."""
    handler = _Recorder(httpx.Response(500))

    with pytest.raises(UpstreamStatusError):
        await _client(handler, max_retries=3).request('GET', '/thing')

    assert handler.calls == 1


async def test_does_not_retry_404():
    handler = _Recorder(httpx.Response(404))

    with pytest.raises(UpstreamStatusError) as caught:
        await _client(handler, max_retries=3).request('GET', '/thing')

    assert caught.value.upstream_status == 404
    assert handler.calls == 1


async def test_retries_are_bounded():
    handler = _Recorder(httpx.Response(503))

    with pytest.raises(UpstreamUnavailableError):
        await _client(handler, max_retries=2).request('GET', '/thing')

    assert handler.calls == 3  # 최초 1회 + 재시도 2회


async def test_retry_after_is_capped():
    """상대가 '10분 뒤에 오라' 고 해도 그만큼 매달리지 않는다."""
    handler = _Recorder(httpx.Response(503, headers={'retry-after': '600'}), httpx.Response(200, json={}))
    client = _client(handler, max_retries=1)

    started = asyncio.get_running_loop().time()
    await client.request('GET', '/thing')
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 6  # MAX_RETRY_AFTER_SECONDS 상한이 걸린다


# ------------------------------------------------------------------ 에러 매핑


async def test_timeout_maps_to_504():
    handler = _Recorder(httpx.ReadTimeout('slow'))

    with pytest.raises(UpstreamTimeoutError) as caught:
        await _client(handler, max_retries=0).request('GET', '/thing')

    assert caught.value.status_code == 504
    assert caught.value.code == 'upstream.timeout'


async def test_connection_failure_maps_to_503():
    handler = _Recorder(httpx.ConnectError('no route'))

    with pytest.raises(UpstreamUnavailableError) as caught:
        await _client(handler, max_retries=0).request('GET', '/thing')

    assert caught.value.status_code == 503


async def test_status_error_defaults_to_502():
    """상대의 404 를 우리 404 로 흘리지 않는다 — gateway 가 의미를 정한다."""
    handler = _Recorder(httpx.Response(404))

    with pytest.raises(UpstreamStatusError) as caught:
        await _client(handler).request('GET', '/thing')

    assert caught.value.status_code == 502


async def test_non_json_body_maps_to_bad_payload():
    handler = _Recorder(httpx.Response(200, text='<html>maintenance</html>'))

    with pytest.raises(UpstreamPayloadError):
        await _client(handler).get_json('/thing')


async def test_errors_do_not_leak_the_upstream_name_into_the_response():
    """규칙 #20 — 어느 서버가 죽었는지는 로그에만 남긴다."""
    handler = _Recorder(httpx.Response(503))

    with pytest.raises(UpstreamUnavailableError) as caught:
        await _client(handler, max_retries=0).request('GET', '/thing')

    assert caught.value.details == {}
    assert caught.value.upstream == 'a'  # 파이썬 속성으로는 남아 있다
