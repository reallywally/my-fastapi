"""예외 → 응답 렌더링 (§2.6).

모든 실패가 같은 모양으로 나간다. 화면이 상태코드별로 다른 파싱을 하지 않아도 되도록.

핸들러가 지켜야 할 것:
- **5xx 는 원인을 본문에 넣지 않는다.** 스택트레이스와 DB 오류 문구는 로그로만 간다.
- 요청 ID 를 본문에도 넣는다. 사용자가 캡처해서 보내면 로그를 바로 찾을 수 있다.
- 언어는 `Accept-Language` 로 정한다 (§2.6).
"""

import logging

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.errors.catalog import negotiate_locale, render
from app.common.errors.exceptions import STATUS_TO_CODE, AppError
from app.common.response import ErrorDetail, ErrorResponse, MsgspecJSONResponse
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _build(request: Request, *, status_code: int, code: str, details: dict) -> MsgspecJSONResponse:
    settings = get_settings()
    locale = negotiate_locale(request.headers.get('accept-language'), settings.default_locale)
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=render(code, locale, settings.default_locale), details=details),
        request_id=request.scope.get('state', {}).get('request_id'),
    )
    return MsgspecJSONResponse(status_code=status_code, content=body.model_dump())


async def handle_app_error(request: Request, exc: AppError) -> MsgspecJSONResponse:
    """업무 예외. 여기까지 온 것은 의도된 실패다 — exception 으로 찍지 않는다."""
    logger.info('app error: %s (%s)', exc.code, request.url.path)
    return _build(request, status_code=exc.status_code, code=exc.code, details=exc.details)


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> MsgspecJSONResponse:
    """FastAPI 내부(404, 405 등)와 직접 던진 HTTPException."""
    code = STATUS_TO_CODE.get(exc.status_code, 'request.invalid')
    return _build(request, status_code=exc.status_code, code=code, details={})


async def handle_validation_error(request: Request, exc: RequestValidationError) -> MsgspecJSONResponse:
    """Pydantic 검증 실패. 어떤 필드가 왜 틀렸는지는 `details.fields` 로 준다."""
    fields = [
        {
            'field': '.'.join(str(part) for part in error['loc'][1:]) or str(error['loc'][0]),
            'reason': error['type'],
        }
        for error in exc.errors()
    ]
    return _build(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code='request.unprocessable',
        details={'fields': fields},
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> MsgspecJSONResponse:
    """예상 못 한 예외. **본문에 원인을 쓰지 않는다** — 내부 구조가 새어나간다."""
    logger.exception('unhandled error at %s %s', request.method, request.url.path, exc_info=exc)
    return _build(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code='internal.error',
        details={},
    )
