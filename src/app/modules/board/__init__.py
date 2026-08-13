"""게시판 컨텍스트 (§4.1).

`board` / `post` / `comment` / `attachment` 는 §3.4 의 vertical slice 지만 **서로
독립적이지 않다.** 댓글은 글 없이 존재할 수 없고 글은 게시판 없이 존재할 수 없다.
셋을 `modules/` 아래 평평하게 두면 모듈 간 참조가 무질서해진다. 그래서 하나의
컨텍스트 안에 슬라이스를 중첩한다.

**내부 의존 방향도 단방향이다:** `attachment`·`comment` → `post` → `board`.
역방향 금지 — `post` 는 자기 댓글 수를 알지만 `comment` 모듈은 모른다 (§4.4).
`.importlinter` 의 `board-internal` 계약이 이걸 못박는다.

`bootstrap/router.py` 는 이 파일의 `router` 하나만 본다.
"""

from fastapi import APIRouter

from app.modules.board.board.router import router as _board_router
from app.modules.board.comment.router import post_router as _comment_post_router
from app.modules.board.comment.router import router as _comment_router
from app.modules.board.post.router import board_router as _post_board_router
from app.modules.board.post.router import router as _post_router

router = APIRouter()

# 순서가 중요하다. `/boards/{slug}` 가 먼저 등록되면 `/boards/{slug}/posts` 요청이
# 그쪽으로 먼저 매칭되지는 않지만(경로 길이가 다르다), 라우트 목록의 읽는 순서는
# 컨텍스트의 의존 방향과 같게 둔다.
router.include_router(_board_router)
router.include_router(_post_board_router)
router.include_router(_post_router)
router.include_router(_comment_post_router)
router.include_router(_comment_router)

__all__ = ['router']
