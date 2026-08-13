"""댓글의 업무 규칙 (§1.2). stateless + 모듈 전역 인스턴스 (§1.3).

이 파일이 §4.1 의 의존 방향을 실제로 쓰는 곳이다: `comment` → `post` → `board`.
역방향은 없다 — `post` 는 자기 댓글 수를 알지만 (§4.4) `comment` 모듈을 모른다.

**`comment_count` 갱신은 댓글 쓰기와 같은 트랜잭션이다** (§4.4). 라우터가 `TxDep` 을
선언했으므로 (§1.1) 둘 중 하나가 실패하면 둘 다 롤백된다. 카운트만 남거나 댓글만
남는 상태가 존재하지 않는다.
"""

from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.common.security import Principal
from app.modules.board.board.service import board_service
from app.modules.board.comment.model import Comment
from app.modules.board.comment.repository import comment_repository
from app.modules.board.comment.schema import (
    CommentPageResponse,
    CommentResponse,
    CreateCommentRequest,
    UpdateCommentRequest,
)
from app.modules.board.post.model import Post, PostStatus
from app.modules.board.post.repository import post_repository


class CommentService:
    @staticmethod
    async def _readable_post(*, db: AsyncConnection, post_id: int) -> Post:
        """댓글은 글에 딸린다. 글을 볼 수 없으면 댓글도 볼 수 없다.

        `post_service.get` 을 부르지 않고 레포지토리로 가는 이유는 §4.1 이다 —
        서비스끼리 부르기 시작하면 §1.3 에서 못 푼다고 한 그 문제로 돌아간다.
        게시판 접근 판정은 규칙을 소유한 `board` 슬라이스에 맡긴다.
        """
        post = await post_repository.get(db, post_id)
        if post is None or post.status is not PostStatus.published:
            raise NotFoundError(code='post.not_found')
        await board_service.readable(db=db, board_id=post.board_id)
        return post

    @classmethod
    async def create(cls, *, db: AsyncConnection, post_id: int, actor: Principal, obj: CreateCommentRequest) -> Comment:
        """작성. 댓글과 카운트가 한 트랜잭션에서 움직인다 (§4.4)."""
        post = await cls._readable_post(db=db, post_id=post_id)

        parent = None
        if obj.parent_id is not None:
            parent = await comment_repository.get(db, obj.parent_id)
            if parent is None or parent.post_id != post.id:
                # 다른 글의 댓글을 부모로 지정하면 트리가 두 글에 걸친다.
                raise NotFoundError(code='comment.parent_not_found')
            if not parent.accepts_replies:
                # §4.2 — 무한 뎁스는 화면에서 감당이 안 된다. 서버에서 막는다.
                raise BadRequestError(code='comment.too_deep')

        comment = await comment_repository.insert(
            db,
            post_id=post.id,
            author_id=actor.id,
            content=obj.content,
            parent_id=parent.id if parent else None,
            parent_path=parent.path if parent else None,
            depth=parent.depth + 1 if parent else 0,
        )
        await post_repository.bump_comment_count(db, post.id, 1)
        return comment

    @staticmethod
    async def get(*, db: AsyncConnection, pk: int) -> Comment:
        comment = await comment_repository.get(db, pk)
        if comment is None:
            raise NotFoundError(code='comment.not_found')
        return comment

    @classmethod
    async def list(cls, *, db: AsyncConnection, post_id: int, cursor: str | None, size: int) -> CommentPageResponse:
        """트리 한 페이지. `path` 순서가 곧 트리 순서다 (§4.2).

        묘비도 실려 나간다 — 자식을 매달고 있는 자리라 빼면 트리가 끊긴다.
        내용 마스킹은 응답 스키마가 한다 (§4.7).
        """
        await cls._readable_post(db=db, post_id=post_id)

        rows = await comment_repository.list_thread(db, post_id=post_id, cursor=cursor, size=size)
        has_next = len(rows) > size
        kept = rows[:size]
        return CommentPageResponse(
            items=[CommentResponse.model_validate(row) for row in kept],
            has_next=has_next,
            # 커서는 **잘라낸 뒤 마지막 항목**에서 뽑는다. 여분으로 읽은 행에서 뽑으면
            # 그 행을 건너뛴다 (`common/pagination.py` 와 같은 판정).
            next_cursor=kept[-1].path if has_next and kept else None,
        )

    @classmethod
    async def update(cls, *, db: AsyncConnection, pk: int, actor: Principal, obj: UpdateCommentRequest) -> Comment:
        comment = await cls.get(db=db, pk=pk)
        if not actor.can_act_on(comment.author_id):
            raise ForbiddenError(code='comment.not_owner')
        if comment.is_removed:
            # 묘비의 내용은 이미 지워졌다. 되살리는 통로를 열어두지 않는다.
            raise ConflictError(code='comment.removed')

        updated = await comment_repository.update(db, pk=comment.id, changes=obj.changes())
        if updated is None:  # pragma: no cover - 같은 트랜잭션 안에서 방금 조회한 행이 사라질 수는 없다
            raise NotFoundError(code='comment.not_found')
        return updated

    @classmethod
    async def delete(cls, *, db: AsyncConnection, pk: int, actor: Principal) -> None:
        """삭제 — **자식이 있으면 묘비, 없으면 soft delete** (§4.7).

        자식이 있는 댓글을 감추면 대댓글이 고아가 된다. 그래서 `deleted`(감사·복구용)와
        `is_removed`(트리 유지용 묘비)를 나눠 뒀고, 여기가 그 둘이 갈리는 유일한 지점이다.

        **묘비는 카운트를 줄이지 않는다.** 화면에 자리가 남아 있으면 그것은 여전히
        한 개의 댓글이다 — 세는 것과 보이는 것이 어긋나면 사용자가 먼저 알아챈다.
        """
        comment = await cls.get(db=db, pk=pk)
        if not actor.can_act_on(comment.author_id):
            raise ForbiddenError(code='comment.not_owner')
        if comment.is_removed:
            return  # 이미 지워진 자리. 두 번 세지 않는다.

        if await comment_repository.count_children(db, comment.id) > 0:
            await comment_repository.mark_removed(db, comment.id)
            return

        await comment_repository.mark_deleted(db, comment.id)
        await post_repository.bump_comment_count(db, comment.post_id, -1)


comment_service = CommentService()
