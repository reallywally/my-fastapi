"""요청 ID 미들웨어 (§0). ASGI 만 태우므로 DB·Redis 가 필요 없다."""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.common.middleware import RequestIDMiddleware, request_id_ctx
from app.core.constants import REQUEST_ID_HEADER


def _build_app() -> Starlette:
    async def echo(request):
        return PlainTextResponse(request_id_ctx.get())

    app = Starlette(routes=[Route('/', echo)])
    app.add_middleware(RequestIDMiddleware)
    return app


async def _get(headers: dict[str, str] | None = None):
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        return await client.get('/', headers=headers or {})


async def test_generates_request_id_when_absent():
    response = await _get()

    assert response.headers[REQUEST_ID_HEADER]
    assert response.text == response.headers[REQUEST_ID_HEADER]


async def test_reuses_valid_client_request_id():
    response = await _get({REQUEST_ID_HEADER: 'abc-123_XYZ'})

    assert response.headers[REQUEST_ID_HEADER] == 'abc-123_XYZ'


@pytest.mark.parametrize(
    'bad',
    [
        'has space',
        'x' * 65,
        'semi;colon',
        'slash/injected',
    ],
)
async def test_rejects_untrustworthy_client_request_id(bad: str):
    """응답 헤더로 되돌려주는 값이므로 클라이언트 입력을 그대로 쓰지 않는다."""
    response = await _get({REQUEST_ID_HEADER: bad})

    assert response.headers[REQUEST_ID_HEADER] != bad


async def test_context_is_reset_after_request():
    await _get()

    assert request_id_ctx.get() == ''
