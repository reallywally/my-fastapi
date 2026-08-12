"""OpenAPI 계약 유지 도구 (§0, §1.5).

생성된 클라이언트의 함수 이름이 라우트 경로에 따라 흔들리지 않게 고정하고,
operation id 충돌을 **기동 시점에** 터뜨린다. 화면을 붙일 때 계약이 깨져 있으면 늦다.

검사는 `app.routes` 순회가 아니라 **생성된 스키마**로 한다. FastAPI 0.141 부터
`include_router` 가 라우트를 평탄화하지 않아서 `app.routes` 에는 내부 래퍼만 남는다.
스키마는 공개 API 이고, 어차피 클라이언트가 보는 것도 그쪽이다.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute


def simplify_operation_id(route: APIRoute) -> str:
    """`tag_함수명` 형태로 고정한다. 기본값(경로+메서드 해시)은 경로만 바꿔도 달라진다."""
    tag = route.tags[0] if route.tags else 'default'
    return f'{tag}_{route.name}'


def ensure_unique_operation_ids(app: FastAPI) -> None:
    """중복 operation id 가 있으면 기동을 실패시킨다.

    스키마를 한 번 만들어보는 부수 효과로 응답 모델 오류도 여기서 먼저 드러난다.
    캐시는 남기지 않는다 — 테스트가 `create_app()` 이후 라우트를 더 붙일 수 있다.
    """
    try:
        schema = app.openapi()
    finally:
        app.openapi_schema = None

    seen: dict[str, str] = {}
    for path, operations in schema.get('paths', {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get('operationId')
            if operation_id is None:
                continue
            location = f'{method.upper()} {path}'
            if operation_id in seen:
                raise ValueError(
                    f'operation id 가 중복이다: {operation_id!r} '
                    f'({seen[operation_id]} vs {location}). 라우트 함수 이름을 바꿔라.'
                )
            seen[operation_id] = location
