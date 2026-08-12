"""커서 페이지네이션 계약 (§4.3, §0).

Phase 5 의 게시판이 쓸 모양이지만 **지금 고정한다.** 목록 응답 형태는 화면이 처음부터
의존하는 계약이라(§0), 나중에 바꾸면 서버와 화면을 같이 고쳐야 한다.

`total` 이 없는 것은 누락이 아니라 결정이다. 대형 게시판에서 `COUNT(*)` 는 매 요청
풀스캔이고, 무한 스크롤에는 애초에 필요 없다.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel, Field

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class CursorParams(BaseModel):
    cursor: int | None = Field(default=None, description='마지막으로 받은 항목의 id. 첫 페이지는 생략한다.')
    size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


def cursor_params(
    cursor: Annotated[int | None, Query(description='마지막으로 받은 항목의 id')] = None,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> CursorParams:
    return CursorParams(cursor=cursor, size=size)


CursorDep = Annotated[CursorParams, Depends(cursor_params)]


class Page[T](BaseModel):
    """`{items, next_cursor, has_next}`. §4.3 의 JSON 과 1:1 이다."""

    items: list[T]
    next_cursor: int | None = None
    has_next: bool = False

    @classmethod
    def of(cls, rows: list[T], *, size: int, cursor_of: Callable[[T], int]) -> 'Page[T]':
        """`limit(size + 1)` 로 읽어온 결과를 페이지로 자른다.

        한 개 더 읽어서 `has_next` 를 판정하는 것이 §4.3 의 방식이다 — 개수를 세지 않는다.
        """
        has_next = len(rows) > size
        items = rows[:size]
        return cls(
            items=items,
            has_next=has_next,
            next_cursor=cursor_of(items[-1]) if has_next and items else None,
        )
