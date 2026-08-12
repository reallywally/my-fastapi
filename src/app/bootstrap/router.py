"""라우터 수집. 각 모듈이 자기를 등록하지 않는다 — 방향이 역류하기 때문 (§2.2)."""

from fastapi import APIRouter, FastAPI

from app.bootstrap import health
from app.core.config import get_settings


def register_routers(app: FastAPI) -> None:
    settings = get_settings()

    # 헬스체크는 버전 prefix 밖에 둔다. 배포 인프라가 쓰는 경로라 버저닝 대상이 아니다.
    app.include_router(health.router)

    api = APIRouter(prefix=settings.api_prefix)
    # Phase 3~5 에서 modules 의 라우터를 여기에 붙인다:
    #   api.include_router(user.router)
    #   api.include_router(board.router)
    app.include_router(api)
