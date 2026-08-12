"""§0 — OpenAPI 스키마가 계약이다. 화면을 붙일 때 클라이언트 생성이 깨지면 안 된다."""

import pytest
from fastapi import APIRouter, FastAPI

from app.bootstrap.app import create_app
from app.common.openapi import ensure_unique_operation_ids, simplify_operation_id


def _operation_ids(app: FastAPI) -> set[str]:
    schema = app.openapi()
    app.openapi_schema = None
    return {
        operation['operationId']
        for operations in schema['paths'].values()
        for operation in operations.values()
        if isinstance(operation, dict) and 'operationId' in operation
    }


def test_operation_ids_are_stable_and_readable():
    """기본값은 경로 해시가 섞여 클라이언트 함수명이 흔들린다. `tag_함수명` 으로 고정한다."""
    operation_ids = _operation_ids(create_app())

    assert 'health_health' in operation_ids
    assert 'health_ready' in operation_ids


@pytest.mark.filterwarnings('ignore:Duplicate Operation ID:UserWarning')
def test_duplicate_operation_ids_fail_at_startup():
    """FastAPI 는 경고만 하고 넘어간다. 경고는 CI 로그에 묻히므로 예외로 승격한다."""
    app = FastAPI(generate_unique_id_function=simplify_operation_id)
    router = APIRouter(tags=['thing'])

    @router.get('/a')
    async def duplicated() -> None: ...

    @router.get('/b', name='duplicated')
    async def other() -> None: ...

    app.include_router(router)

    with pytest.raises(ValueError, match='중복'):
        ensure_unique_operation_ids(app)


def test_openapi_schema_builds():
    """스키마 생성 자체가 깨지지 않는지 — 응답 모델 오류는 여기서 먼저 터진다."""
    app = create_app()
    schema = app.openapi()

    assert schema['info']['title'] == 'my-fastapi'
    assert '/health' in schema['paths']
    assert '/health/ready' in schema['paths']


def test_validation_does_not_leave_a_stale_schema_cache():
    """검사 때문에 스키마가 굳어버리면, 이후에 붙는 라우트가 문서에서 사라진다."""
    app = create_app()

    assert app.openapi_schema is None
