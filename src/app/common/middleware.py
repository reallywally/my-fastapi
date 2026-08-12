"""공용 미들웨어. 도메인을 모른다 (§2.2).

등록은 `bootstrap/middleware.py` 가 한다 — 여기는 구현만 둔다.
"""

from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.constants import REQUEST_ID_HEADER, REQUEST_ID_MAX_LENGTH

#: 로깅·감사에서 현재 요청 ID 를 읽는 지점. 로거 필터가 이 값을 붙인다.
request_id_ctx: ContextVar[str] = ContextVar('request_id', default='')


def _sanitize(value: str | None) -> str | None:
    """클라이언트가 보낸 요청 ID 를 그대로 신뢰하지 않는다.

    응답 헤더로 되돌려주는 값이므로 길이와 문자셋을 제한한다.
    """
    if not value or len(value) > REQUEST_ID_MAX_LENGTH:
        return None
    if not all(c.isalnum() or c in '-_' for c in value):
        return None
    return value


class RequestIDMiddleware:
    """요청마다 ID 를 부여하고 응답 헤더로 되돌려준다.

    `BaseHTTPMiddleware` 를 쓰지 않는다 — 스트리밍 응답과 백그라운드 태스크에서
    컨텍스트가 어긋나는 알려진 문제가 있다. 순수 ASGI 로 짠다.
    """

    def __init__(self, app: ASGIApp, header_name: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request_id = _sanitize(Headers(scope=scope).get(self.header_name)) or uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id
        token = request_id_ctx.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message['type'] == 'http.response.start':
                MutableHeaders(scope=message).append(self.header_name, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_ctx.reset(token)
