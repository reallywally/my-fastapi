"""composition root — 조립만 한다. 업무 로직 0 (§5).

`create_app()` 을 호출해도 연결은 열리지 않는다. 자원은 lifespan 이 만든다 (§2.1).
그래서 테스트는 이 함수를 부른 뒤 `app.state` 를 가짜로 채워 넣을 수 있다.
"""

from fastapi import FastAPI

from app.bootstrap.lifespan import lifespan
from app.bootstrap.middleware import register_middleware
from app.bootstrap.router import register_routers
from app.common.openapi import ensure_unique_operation_ids, simplify_operation_id
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = not settings.is_production

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        generate_unique_id_function=simplify_operation_id,
        openapi_url=f'{settings.api_prefix}/openapi.json' if docs_enabled else None,
        docs_url='/docs' if docs_enabled else None,
        redoc_url=None,
    )

    register_middleware(app)
    register_routers(app)

    # 계약 위반은 기동 시점에 터뜨린다 (§0).
    ensure_unique_operation_ids(app)

    return app
