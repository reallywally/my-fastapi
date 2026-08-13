"""게시판의 업무 규칙 (§1.2). stateless + 모듈 전역 인스턴스 (§1.3).

게시판을 만드는 것은 관리자다. 그 판정은 라우터가 `Principal` 로 하고 (§4.6),
여기는 "누가" 가 아니라 "무엇이 유효한가" 만 안다.
"""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.common.security import Principal
from app.modules.board.board.model import Board
from app.modules.board.board.repository import board_repository
from app.modules.board.board.schema import CreateBoardRequest, UpdateBoardRequest


class BoardService:
    @staticmethod
    async def create(*, db: AsyncConnection, obj: CreateBoardRequest, actor: Principal) -> Board:
        """게시판 생성은 관리자만.

        소유권이 아니라 **역할** 판정이라 `can_act_on` 이 아니다. 게시판에는 주인이 없다.
        """
        if not actor.is_superuser:
            raise ForbiddenError(code='board.admin_only')

        if await board_repository.get_by_slug(db, obj.slug) is not None:
            raise ConflictError(code='board.slug_taken')

        try:
            return await board_repository.insert(
                db,
                slug=obj.slug,
                name=obj.name,
                description=obj.description,
                read_role=obj.read_role,
                write_role=obj.write_role,
                allow_comment=obj.allow_comment,
                allow_attachment=obj.allow_attachment,
                display_order=obj.display_order,
            )
        except IntegrityError as exc:
            # 확인과 삽입 사이의 경합. 어느 쪽이 겹쳤는지는 unique 제약이 하나뿐이라 분명하다.
            raise ConflictError(code='board.slug_taken') from exc

    @staticmethod
    async def get_by_slug(*, db: AsyncConnection, slug: str) -> Board:
        board = await board_repository.get_by_slug(db, slug)
        if board is None:
            raise NotFoundError(code='board.not_found')
        return board

    @staticmethod
    def assert_readable(board: Board) -> None:
        """게시판 읽기 접근 판정 (§4.6). **접근 규칙은 board 슬라이스가 소유한다.**

        `deps.py` 의 `require_board` 는 `slug` 로 들어오는 경로에서 이걸 쓰고,
        `post` 와 `comment` 는 이미 손에 든 `board_id` 로 이걸 쓴다. 규칙이 두 벌이
        되면 한쪽만 고치는 날이 오고, 그날 비공개 게시판이 열린다.

        지금 판정하는 것은 `read_role == 'anonymous'` 하나뿐이다 — 역할 계층은
        Phase 5 이고, 주체가 없으면 통과시킬 근거도 없다.
        """
        if not board.is_public:
            raise UnauthorizedError(code='auth.unauthorized')

    @classmethod
    async def readable(cls, *, db: AsyncConnection, board_id: int) -> Board:
        """`board_id` 로 게시판을 읽고 접근을 검사한다. `post`·`comment` 가 쓴다."""
        board = await board_repository.get(db, board_id)
        if board is None:
            # 글은 살아 있는데 게시판이 지워진 경우. 글로 가는 경로가 없어야 한다 (§4.7).
            raise NotFoundError(code='board.not_found')
        cls.assert_readable(board)
        return board

    @staticmethod
    async def list(*, db: AsyncConnection) -> list[Board]:
        return await board_repository.list_all(db)

    @classmethod
    async def update(cls, *, db: AsyncConnection, slug: str, obj: UpdateBoardRequest, actor: Principal) -> Board:
        board = await cls.get_by_slug(db=db, slug=slug)
        if not actor.is_superuser:
            raise ForbiddenError(code='board.admin_only')

        changes: dict[str, Any] = dict(obj.changes())
        updated = await board_repository.update(db, pk=board.id, changes=changes)
        if updated is None:  # pragma: no cover - 같은 트랜잭션 안에서 방금 조회한 행이 사라질 수는 없다
            raise NotFoundError(code='board.not_found')
        return updated

    @classmethod
    async def delete(cls, *, db: AsyncConnection, slug: str, actor: Principal) -> None:
        """게시판 삭제. 글은 손대지 않는다.

        §4.7 의 "글을 지워도 댓글은 손대지 않는다" 와 같은 결이다 — 게시판이 안 보이면
        글 목록으로 가는 경로가 없고, 복구할 때 통째로 살아난다.
        """
        board = await cls.get_by_slug(db=db, slug=slug)
        if not actor.is_superuser:
            raise ForbiddenError(code='board.admin_only')
        await board_repository.mark_deleted(db, board.id)


board_service = BoardService()
