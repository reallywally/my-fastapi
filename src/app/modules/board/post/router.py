"""HTTP 만 다룬다 (§1.2). 검증·직렬화·상태코드.

**경로가 두 갈래다** (§4.10). 글 작성은 `slug` 하위에서 하지만 조회·수정·삭제는
`/posts/{id}` 로 평평하게 간다 — 글 id 가 전역 유일한데 board 를 다시 태우면 경로만
길어지고 검증만 늘어난다.

그래서 라우터도 둘이다. `board_router` 는 `/boards/{slug}/posts` 를, `router` 는
`/posts/{id}` 를 맡는다. 게시판 접근 검사(`BoardReadDep`)가 필요한 것은 앞쪽뿐이다.

`PrincipalDep` 은 지금 `modules/user/deps.py` 에 있다. Phase 5 에서 `modules/auth/deps.py`
로 옮겨가고, 그때 이 import 한 줄이 바뀐다 (§4.1 — 컨텍스트 간에는 주체만 오간다).
"""

from fastapi import APIRouter, status

from app.common.db import ConnDep, TxDep
from app.common.pagination import CursorDep, Page
from app.modules.board.board.deps import BoardReadDep, BoardWriteDep
from app.modules.board.post.schema import CreatePost, PostOut, PostSummary, UpdatePost
from app.modules.board.post.service import post_service
from app.modules.user.deps import PrincipalDep

#: `/boards/{slug}/posts` — 게시판 컨텍스트가 필요한 경로
board_router = APIRouter(prefix='/boards/{slug}/posts', tags=['post'])

#: `/posts/{pk}` — 글 id 하나로 충분한 경로
router = APIRouter(prefix='/posts', tags=['post'])


@board_router.get('', summary='글 목록 (커서 페이지네이션)')
async def list_posts(db: ConnDep, board: BoardReadDep, page: CursorDep) -> Page[PostSummary]:
    return await post_service.list(db=db, board_id=board.id, cursor=page.cursor, size=page.size)


@board_router.post('', status_code=status.HTTP_201_CREATED, summary='글 작성')
async def create_post(db: TxDep, board: BoardWriteDep, obj: CreatePost, actor: PrincipalDep) -> PostOut:
    post = await post_service.create(db=db, board_id=board.id, actor=actor, obj=obj)
    return PostOut.model_validate(post)


@router.get('/{pk}', summary='글 상세')
async def get_post(db: ConnDep, pk: int) -> PostOut:
    post = await post_service.get(db=db, pk=pk)
    return PostOut.model_validate(post)


@router.patch('/{pk}', summary='글 수정 (본인 또는 관리자)')
async def update_post(db: TxDep, pk: int, obj: UpdatePost, actor: PrincipalDep) -> PostOut:
    post = await post_service.update(db=db, pk=pk, actor=actor, obj=obj)
    return PostOut.model_validate(post)


@router.delete('/{pk}', status_code=status.HTTP_204_NO_CONTENT, summary='글 삭제 (본인 또는 관리자)')
async def delete_post(db: TxDep, pk: int, actor: PrincipalDep) -> None:
    await post_service.delete(db=db, pk=pk, actor=actor)
