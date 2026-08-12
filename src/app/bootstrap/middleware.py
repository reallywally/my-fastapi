"""미들웨어 등록. 구현은 `common/middleware.py` 에 있다 (bootstrap 은 조립만).

**순서 주의:** `add_middleware` 는 앞에 끼워 넣는다. 나중에 추가한 것이 바깥쪽이다.
CORS 를 바깥에 두어야 preflight 를 먼저 끊고, 에러 응답에도 CORS 헤더가 붙는다.
"""

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.common.middleware import RequestIDMiddleware
from app.core.config import get_settings
from app.core.constants import REQUEST_ID_HEADER


def register_middleware(app: FastAPI) -> None:
    settings = get_settings()

    app.add_middleware(RequestIDMiddleware)  # 안쪽

    if settings.cors_allow_origins:
        app.add_middleware(  # 바깥쪽
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=['*'],
            allow_headers=['*'],
            expose_headers=[REQUEST_ID_HEADER],
        )
