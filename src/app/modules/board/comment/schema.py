"""댓글의 요청/응답 계약 (§1.2, §0).

**묘비 마스킹은 여기서 한다** (§4.7). 서비스가 응답 형태를 신경 쓰기 시작하면
§2.7 의 누수와 같은 문제다 — 서비스는 "지워졌다" 만 알고, 그것을 화면에 어떻게
보여줄지는 이 계층이 정한다.

목록 응답의 커서가 `str` 인 것은 정렬 키가 `path` 이기 때문이다. 모양은 §4.3 의
`{items, next_cursor, has_next}` 그대로다 — 화면이 다루는 방식이 같아야 한다.
"""

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CommentContent = Annotated[str, Field(min_length=1, max_length=10_000)]

#: 지워진 댓글 자리에 남기는 본문. 화면은 `is_removed` 로 분기하지만, 문구가 비어
#: 있으면 레이아웃이 무너지는 화면도 있어서 값 자체를 준다.
REMOVED_CONTENT = '삭제된 댓글입니다.'


class CreateCommentRequest(BaseModel):
    content: CommentContent
    #: 답글이면 부모 댓글 id. 최상위 댓글이면 생략한다.
    parent_id: int | None = None


class UpdateCommentRequest(BaseModel):
    content: CommentContent

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True, exclude_none=True)


class CommentResponse(BaseModel):
    """트리의 한 항목. 평평한 목록이고 `depth` 로 들여쓴다.

    `author_id` 가 `None` 일 수 있는 것은 묘비 때문이다 — 지워진 댓글은 작성자를
    익명화한다.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    parent_id: int | None
    author_id: int | None
    content: str
    depth: int
    is_removed: bool
    created_at: datetime

    @model_validator(mode='after')
    def _mask_removed(self) -> Self:
        """§4.7 — 묘비는 트리에 남지만 내용과 작성자는 지운다.

        행에는 원본이 그대로 있다(감사·복구용). 나가는 응답에서만 가린다.
        """
        if self.is_removed:
            self.content = REMOVED_CONTENT
            self.author_id = None
        return self


class CommentPageResponse(BaseModel):
    """댓글 트리 한 페이지.

    `Page[T]` 를 쓰지 않는 이유는 커서 타입 하나뿐이다. 글 목록의 커서는 `id`(정수)고
    댓글 트리의 커서는 `path`(문자열)다 — 정렬 키가 곧 커서라야 keyset 이 성립한다.
    `Page[T]` 의 커서를 `int | str` 로 넓히면 모든 목록의 계약이 같이 흐려진다.
    """

    items: list[CommentResponse]
    next_cursor: str | None = None
    has_next: bool = False
