"""HTTP 만 다룬다 (§1.2). 검증·직렬화·상태코드.

**경로가 두 갈래다** (§4.10). 목록·작성은 글에 딸리므로 `/posts/{post_id}/comments` 고,
수정·삭제는 댓글 id 하나로 충분하므로 `/comments/{pk}` 다.

작성은 `TxDep` 이다 — 댓글 삽입과 `comment_count` 갱신이 하나의 트랜잭션이어야
한다 (§4.4, §1.1). 하나만 성공한 상태가 존재하면 안 된다.
"""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.common.db import ConnDep, TxDep
from app.common.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.modules.board.comment.schema import (
    CommentPageResponse,
    CommentResponse,
    CreateCommentRequest,
    UpdateCommentRequest,
)
from app.modules.board.comment.service import comment_service
from app.modules.user.deps import PrincipalDep

#: `/posts/{post_id}/comments` — 글에 딸린 경로
post_router = APIRouter(prefix='/posts/{post_id}/comments', tags=['comment'])

#: `/comments/{pk}` — 댓글 id 하나로 충분한 경로
router = APIRouter(prefix='/comments', tags=['comment'])


@post_router.get('', summary='댓글 트리 (path 커서)')
async def list_comments(
    db: ConnDep,
    post_id: int,
    cursor: Annotated[str | None, Query(description='마지막으로 받은 댓글의 path')] = None,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> CommentPageResponse:
    return await comment_service.list(db=db, post_id=post_id, cursor=cursor, size=size)


@post_router.post('', status_code=status.HTTP_201_CREATED, summary='댓글 작성')
async def create_comment(db: TxDep, post_id: int, obj: CreateCommentRequest, actor: PrincipalDep) -> CommentResponse:
    comment = await comment_service.create(db=db, post_id=post_id, actor=actor, obj=obj)
    return CommentResponse.model_validate(comment)


@router.patch('/{pk}', summary='댓글 수정 (본인)')
async def update_comment(db: TxDep, pk: int, obj: UpdateCommentRequest, actor: PrincipalDep) -> CommentResponse:
    comment = await comment_service.update(db=db, pk=pk, actor=actor, obj=obj)
    return CommentResponse.model_validate(comment)


@router.delete('/{pk}', status_code=status.HTTP_204_NO_CONTENT, summary='댓글 삭제 (본인 또는 관리자)')
async def delete_comment(db: TxDep, pk: int, actor: PrincipalDep) -> None:
    await comment_service.delete(db=db, pk=pk, actor=actor)
