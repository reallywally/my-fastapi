"""HTTP 만 다룬다 (§1.2). 검증·직렬화·상태코드.

게시판 목록·상세는 누구나 본다 — 목록이 곧 메뉴라 로그인 전에도 보여야 한다.
**게시판 안의 글**을 볼 수 있는지는 별개이고, 그건 `deps.py` 의 `BoardReadDep` 이 판정한다.

생성·수정·삭제는 관리자만이라 `PrincipalDep` 가 401 을 내는 Phase 5 까지 열리지 않는다.
라우트를 지금 노출하는 이유는 §0 — OpenAPI 계약이 확정되어야 화면 작업을 병행할 수 있다.
"""

from fastapi import APIRouter, status

from app.common.db import ConnDep, TxDep
from app.modules.board.board.schema import BoardOut, CreateBoard, UpdateBoard
from app.modules.board.board.service import board_service
from app.modules.user.deps import PrincipalDep

router = APIRouter(prefix='/boards', tags=['board'])


@router.get('', summary='게시판 목록')
async def list_boards(db: ConnDep) -> list[BoardOut]:
    boards = await board_service.list(db=db)
    return [BoardOut.model_validate(board) for board in boards]


@router.get('/{slug}', summary='게시판 상세')
async def get_board(db: ConnDep, slug: str) -> BoardOut:
    board = await board_service.get_by_slug(db=db, slug=slug)
    return BoardOut.model_validate(board)


@router.post('', status_code=status.HTTP_201_CREATED, summary='게시판 생성 (관리자)')
async def create_board(db: TxDep, obj: CreateBoard, actor: PrincipalDep) -> BoardOut:
    board = await board_service.create(db=db, obj=obj, actor=actor)
    return BoardOut.model_validate(board)


@router.patch('/{slug}', summary='게시판 수정 (관리자)')
async def update_board(db: TxDep, slug: str, obj: UpdateBoard, actor: PrincipalDep) -> BoardOut:
    board = await board_service.update(db=db, slug=slug, obj=obj, actor=actor)
    return BoardOut.model_validate(board)


@router.delete('/{slug}', status_code=status.HTTP_204_NO_CONTENT, summary='게시판 삭제 (관리자)')
async def delete_board(db: TxDep, slug: str, actor: PrincipalDep) -> None:
    await board_service.delete(db=db, slug=slug, actor=actor)
