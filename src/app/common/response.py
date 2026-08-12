"""응답 직렬화와 에러 응답 계약 (§1.5, §0).

**성공 응답은 감싸지 않는다.** §4.3 이 목록 응답을 `{items, next_cursor, has_next}`
로 이미 고정했다. 여기에 `{data: ...}` 를 한 겹 더 씌우면 문서와 어긋나고, 화면은
의미 없는 껍질을 매번 벗겨야 한다. 표준화가 필요한 것은 **에러**와 **페이지**다.

에러 응답은 §0 의 계약이다. 화면은 `error.code` 로 분기하고 `error.message` 를 띄운다.
`request_id` 는 사용자가 캡처해서 보내오면 로그를 바로 찾을 수 있게 하는 값이다.
"""

from typing import Any

import msgspec
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

_encoder = msgspec.json.Encoder()


class MsgspecJSONResponse(JSONResponse):
    """orjson 보다 빠르고 Pydantic v2 와 궁합이 좋다 (§1.5).

    FastAPI 가 이미 모델을 jsonable 로 바꿔서 넘겨주므로, 여기서는 인코딩만 한다.
    """

    media_type = 'application/json'

    def render(self, content: Any) -> bytes:
        return _encoder.encode(content)


class ErrorDetail(BaseModel):
    code: str = Field(description='화면이 분기에 쓰는 안정적인 식별자. 메시지가 아니다.')
    message: str = Field(description='Accept-Language 로 해석된 사람이 읽는 문구.')
    details: dict[str, Any] = Field(default_factory=dict, description='필드 오류 등 부가 정보.')


class ErrorResponse(BaseModel):
    """모든 4xx/5xx 의 본문 형태. 예외 종류와 무관하게 동일하다."""

    error: ErrorDetail
    request_id: str | None = Field(default=None, description='X-Request-ID 와 같은 값.')
