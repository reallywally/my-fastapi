"""글의 요청/응답 계약 (§1.2, §0).

**목록과 상세의 응답이 다르다.** `PostSummaryResponse` 에는 `content` 가 없다 — 목록 20개에
본문을 다 실으면 응답이 메가바이트 단위가 되고, 화면은 그걸 쓰지도 않는다.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.modules.board.post.model import PostStatus

Title = Annotated[str, Field(min_length=1, max_length=200, examples=['공지: 서비스 점검 안내'])]
#: 상한을 두는 이유는 성능이 아니라 DoS 다. 본문 없는 상한은 곧 무제한 업로드다.
Content = Annotated[str, Field(min_length=1, max_length=100_000)]


class CreatePostRequest(BaseModel):
    title: Title
    content: Content
    status: PostStatus = PostStatus.published


class UpdatePostRequest(BaseModel):
    """부분 수정. 준 필드만 바뀐다.

    `board_id` 는 없다. 글을 다른 게시판으로 옮기는 것은 권한 판정이 달라지는 동작이라
    (§4.6) 별도 엔드포인트여야 한다.
    """

    title: Title | None = None
    content: Content | None = None
    status: PostStatus | None = None

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True, exclude_none=True)


class PostSummaryResponse(BaseModel):
    """목록 항목. 본문이 없다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    board_id: int
    author_id: int
    title: str
    is_pinned: bool
    status: PostStatus
    view_count: int
    comment_count: int
    created_at: datetime


class PostResponse(PostSummaryResponse):
    """상세. 목록 항목에 본문을 더한 것이다."""

    content: str
    updated_at: datetime
