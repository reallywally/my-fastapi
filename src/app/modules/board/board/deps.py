"""게시판 단위 접근 검사 (§4.6).

**게시판마다 권한이 다르다.** 공지사항은 누구나 읽고 관리자만 쓰고, 회원 게시판은
로그인해야 읽는다. 그 판정을 라우터마다 손으로 쓰면 §2.4 의 `deleted = 0` 과 같은
운명을 맞는다 — 어딘가에서 반드시 빠뜨린다. 그래서 `Depends` 다.

    @router.get('/{slug}/posts')
    async def list_posts(board: BoardReadDep, ...): ...   # slug 해석 + 접근 검사 완료

**지금은 `read_role == 'anonymous'` 만 판정한다.** 역할 계층(`rbac.satisfies`)은
Phase 5 다 — 주체가 없으니 판정할 것도 없다. 그 외에는 전부 401 을 낸다.

401 은 안전한 쪽으로 틀린다. 여기서 통과시켜 두면 인증이 붙기 전까지 비공개 게시판이
열려 있게 되고, 그 구멍은 조용하다.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import Depends

from app.common.db import ConnDep
from app.common.errors import UnauthorizedError
from app.modules.board.board.model import ANONYMOUS, Board
from app.modules.board.board.service import board_service

Permission = Literal['read', 'write']


def require_board(permission: Permission) -> Callable[..., Coroutine[Any, Any, Board]]:
    """`slug` 를 게시판으로 바꾸고, 그 게시판에 대한 접근을 검사한다.

    게시판이 없으면 404 가 먼저 난다 — 존재 여부는 권한과 무관한 사실이고, 없는
    게시판에 401 을 내면 클라이언트가 로그인해도 달라지지 않는다.
    """

    async def _dep(db: ConnDep, slug: str) -> Board:
        board = await board_service.get_by_slug(db=db, slug=slug)
        if permission == 'read':
            board_service.assert_readable(board)
        elif board.write_role != ANONYMOUS:
            # 역할 판정은 Phase 5. 주체가 없는 지금은 통과시킬 근거가 없다.
            raise UnauthorizedError(code='auth.unauthorized')
        return board

    return _dep


#: 읽기 접근이 확인된 게시판. `read_role` 이 anonymous 인 동안만 통과한다.
BoardReadDep = Annotated[Board, Depends(require_board('read'))]

#: 쓰기 접근이 확인된 게시판. 기본값(`member`)에서는 Phase 5 까지 항상 401 이다.
BoardWriteDep = Annotated[Board, Depends(require_board('write'))]
