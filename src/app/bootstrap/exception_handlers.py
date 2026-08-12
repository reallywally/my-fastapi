"""예외 핸들러 등록. 구현은 `common/errors/handlers.py` 에 있다 (bootstrap 은 조립만).

`Exception` 핸들러를 마지막에 다는 이유: Starlette 은 이걸 특별 취급해서
"어떤 것도 잡지 못했을 때" 로 쓴다. 이게 없으면 예상 못 한 예외가 스택트레이스를
그대로 본문에 실어 내보낸다.
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.errors.exceptions import AppError
from app.common.errors.handlers import (
    handle_app_error,
    handle_http_exception,
    handle_unexpected_error,
    handle_validation_error,
)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unexpected_error)
