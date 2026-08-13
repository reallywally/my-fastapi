"""에러 응답 계약을 라우터 관통으로 확인한다 (§0, §2.6).

화면은 이 모양에 의존한다. 여기가 깨지면 서버가 아니라 화면이 먼저 깨진다.
"""

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.bootstrap.exception_handlers import register_exception_handlers
from app.bootstrap.middleware import register_middleware
from app.common.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.constants import REQUEST_ID_HEADER

pytestmark = pytest.mark.asyncio(loop_scope='session')


class _Payload(BaseModel):
    count: int


def _build_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter(tags=['probe'])

    @router.get('/not-found')
    async def not_found() -> None:
        raise NotFoundError(code='post.not_found')

    @router.get('/unknown-code')
    async def unknown_code() -> None:
        # 카탈로그에 절대 넣지 않는 코드. 폴백 경로를 검증하기 위한 것이다.
        raise NotFoundError(code='probe.never_registered')

    @router.get('/forbidden')
    async def forbidden() -> None:
        raise ForbiddenError(code='post.not_owner', details={'post_id': 7})

    @router.get('/conflict')
    async def conflict() -> None:
        raise ConflictError()

    @router.get('/http-exception')
    async def http_exception() -> None:
        raise HTTPException(status_code=405)

    @router.post('/validated')
    async def validated(payload: _Payload) -> dict[str, int]:
        return {'count': payload.count}

    @router.get('/boom')
    async def boom() -> None:
        raise RuntimeError('leaky internal detail: db password is hunter2')

    app.include_router(router)
    register_middleware(app)
    register_exception_handlers(app)
    return app


@pytest.fixture(scope='session')
def probe_app(settings) -> FastAPI:
    return _build_app()


async def _get(app: FastAPI, path: str, **kwargs):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url='http://probe') as client:
        method = kwargs.pop('method', 'GET')
        return await client.request(method, path, **kwargs)


async def test_app_error_renders_the_standard_shape(probe_app):
    response = await _get(probe_app, '/not-found')

    assert response.status_code == 404
    body = response.json()
    assert body['error']['code'] == 'post.not_found'
    assert body['error']['details'] == {}
    assert set(body) == {'error', 'request_id'}


async def test_details_are_passed_through(probe_app):
    response = await _get(probe_app, '/forbidden')

    assert response.status_code == 403
    assert response.json()['error']['details'] == {'post_id': 7}


async def test_message_is_localised_by_accept_language(probe_app):
    korean = await _get(probe_app, '/conflict', headers={'Accept-Language': 'ko'})
    english = await _get(probe_app, '/conflict', headers={'Accept-Language': 'en-US,en;q=0.9'})

    assert korean.json()['error']['message'] == '이미 존재하는 값입니다.'
    assert english.json()['error']['message'] == 'That value already exists.'
    # 코드는 언어와 무관하게 같다 — 화면이 분기하는 값이니까 (§0).
    assert korean.json()['error']['code'] == english.json()['error']['code']


async def test_unknown_code_falls_back_to_the_code_itself(probe_app):
    """카탈로그에 없는 코드는 500 이 아니라 코드 자체가 나와야 한다.

    화면은 `code` 로 분기하므로 (§0) 문구가 없는 것은 장애가 아니다. 번역 누락은
    `test_errors.py` 가 따로 잡는다.
    """
    response = await _get(probe_app, '/unknown-code')

    assert response.status_code == 404
    assert response.json()['error']['message'] == 'probe.never_registered'


async def test_http_exception_maps_to_a_code(probe_app):
    response = await _get(probe_app, '/http-exception')

    assert response.status_code == 405
    assert response.json()['error']['code'] == 'request.method_not_allowed'


async def test_validation_error_reports_the_offending_fields(probe_app):
    response = await _get(probe_app, '/validated', method='POST', json={'count': 'not-a-number'})

    assert response.status_code == 422
    body = response.json()
    assert body['error']['code'] == 'request.unprocessable'
    assert body['error']['details']['fields'] == [{'field': 'count', 'reason': 'int_parsing'}]


async def test_unexpected_error_does_not_leak_internals(probe_app):
    response = await _get(probe_app, '/boom')

    assert response.status_code == 500
    assert response.json()['error']['code'] == 'internal.error'
    assert 'hunter2' not in response.text
    assert 'RuntimeError' not in response.text


async def test_error_body_carries_the_request_id(probe_app):
    response = await _get(probe_app, '/not-found')

    assert response.json()['request_id'] == response.headers[REQUEST_ID_HEADER]
