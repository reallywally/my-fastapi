"""글의 업무 규칙 (§1.2). stateless + 모듈 전역 인스턴스 (§1.3).

지키는 것:
- **`commit()` 하지 않는다** (§1.1, 규칙 #1)
- **`Request` 를 받지 않는다** (§2.7, 규칙 #5)
- **소유권 비교는 넘겨받은 principal 로** 한다 (§4.6, 규칙 #14)

마지막 항목이 이 파일의 핵심이다. §2.7 의 FBA 버그가 정확히 이 모양에서 났다 —
서비스가 `Request` 를 받으면 `request.user.id` 와 비교해야 할 것을 조회한 행의 `id` 와
비교하기 쉽고, 그러면 조건이 상수가 되어 권한 검사가 통째로 죽는다. 비교 대상은
**라우터가 넘긴 `actor`** 이고, 소유자는 `post.author_id` 다.
"""

from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.errors import ForbiddenError, NotFoundError
from app.common.pagination import Page
from app.common.security import Principal
from app.modules.board.post.model import Post, PostStatus
from app.modules.board.post.repository import post_repository
from app.modules.board.post.schema import CreatePost, PostSummary, UpdatePost


class PostService:
    @staticmethod
    async def create(*, db: AsyncConnection, board_id: int, actor: Principal, obj: CreatePost) -> Post:
        """작성. 게시판 쓰기 권한은 라우터의 `BoardWriteDep` 가 이미 확인했다 (§4.6)."""
        return await post_repository.insert(
            db,
            board_id=board_id,
            author_id=actor.id,
            title=obj.title,
            content=obj.content,
            status=obj.status,
        )

    @staticmethod
    async def get(*, db: AsyncConnection, pk: int) -> Post:
        """상세.

        **초안은 없는 것으로 취급한다.** 작성자 본인에게 보여주려면 주체가 필요한데
        (Phase 5), 그때까지 열어두면 남의 초안이 공개된다. 404 는 안전한 쪽으로 틀린다.

        조회수는 여기서 올리지 않는다 — 읽기가 쓰기가 되면 안 된다 (§4.5).
        `view_counter` 가 Phase 4 의 다음 항목이다.
        """
        post = await post_repository.get(db, pk)
        if post is None or post.status is not PostStatus.published:
            raise NotFoundError(code='post.not_found')
        return post

    @staticmethod
    async def list(*, db: AsyncConnection, board_id: int, cursor: int | None, size: int) -> Page[PostSummary]:
        """§4.3 — keyset 목록. 고정글은 **첫 페이지에만** 앞에 붙인다.

        고정글을 매 페이지에 붙이면 스크롤할 때마다 같은 글이 반복되고, 정렬 키에
        섞으면 커서가 깨진다. 그래서 커서 시퀀스와 완전히 분리한다 —
        `has_next` 와 `next_cursor` 는 고정글이 아닌 쪽에서만 나온다.
        """
        rows = await post_repository.list_page(db, board_id=board_id, cursor=cursor, size=size)
        page = Page[PostSummary].of(
            rows,
            size=size,
            cursor_of=lambda row: row.id,
            to_item=PostSummary.model_validate,
        )
        if cursor is None:
            pinned = await post_repository.list_pinned(db, board_id=board_id)
            page.items = [PostSummary.model_validate(row) for row in pinned] + page.items
        return page

    @classmethod
    async def update(cls, *, db: AsyncConnection, pk: int, actor: Principal, obj: UpdatePost) -> Post:
        post = await cls.get(db=db, pk=pk)
        if not actor.can_act_on(post.author_id):
            raise ForbiddenError(code='post.not_owner')

        changes = obj.changes()
        if not changes:
            return post

        updated = await post_repository.update(db, pk=post.id, changes=changes)
        if updated is None:  # pragma: no cover - 같은 트랜잭션 안에서 방금 조회한 행이 사라질 수는 없다
            raise NotFoundError(code='post.not_found')
        return updated

    @classmethod
    async def delete(cls, *, db: AsyncConnection, pk: int, actor: Principal) -> None:
        """삭제. hard delete 가 아니라 `deleted = id` (§1.4).

        **댓글은 손대지 않는다** (§4.7). 글이 안 보이면 댓글로 가는 경로가 없고,
        복구할 때 통째로 살아난다.
        """
        post = await cls.get(db=db, pk=pk)
        if not actor.can_act_on(post.author_id):
            raise ForbiddenError(code='post.not_owner')
        await post_repository.mark_deleted(db, post.id)


post_service = PostService()
