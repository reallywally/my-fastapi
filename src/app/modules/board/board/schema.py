"""게시판의 요청/응답 계약 (§1.2, §0).

`BoardResponse` 은 허용 목록이다. 모델에 필드를 늘려도 응답에 새어나가지 않는다.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Slug = Annotated[
    str,
    Field(min_length=2, max_length=50, pattern=r'^[a-z0-9-]+$', examples=['notice', 'free-talk']),
]
BoardName = Annotated[str, Field(min_length=1, max_length=100, examples=['공지사항'])]
Role = Annotated[str, Field(min_length=1, max_length=50, examples=['anonymous', 'member'])]


class CreateBoardRequest(BaseModel):
    slug: Slug
    name: BoardName
    description: str | None = None
    read_role: Role = 'anonymous'
    write_role: Role = 'member'
    allow_comment: bool = True
    allow_attachment: bool = True
    display_order: int = 0


class UpdateBoardRequest(BaseModel):
    """부분 수정. 준 필드만 바뀐다 — `None` 과 '생략' 을 구분해야 해서 전부 Optional 이다.

    `slug` 는 없다. URL 식별자가 바뀌면 그 게시판을 가리키던 모든 링크가 깨진다.
    """

    name: BoardName | None = None
    description: str | None = None
    read_role: Role | None = None
    write_role: Role | None = None
    allow_comment: bool | None = None
    allow_attachment: bool | None = None
    display_order: int | None = None

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True, exclude_none=True)


class BoardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    description: str | None
    read_role: str
    write_role: str
    allow_comment: bool
    allow_attachment: bool
    display_order: int
