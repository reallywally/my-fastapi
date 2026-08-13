"""댓글 슬라이스의 업무 규칙을 실 DB 로 검증한다 (§4.2, §4.4, §4.7).

세 가지가 이 파일의 핵심이다:

1. **`path` 순서가 곧 트리 순서다.** 부모 바로 뒤에 그 답글이 온다
2. **`comment_count` 가 댓글과 같은 트랜잭션에서 움직인다.** 롤백되면 카운트도 롤백된다
3. **자식 있는 댓글 삭제는 묘비다.** 감추면 대댓글이 고아가 된다
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import one_or_none, select_rows
from app.common.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.common.security import Principal
from app.modules.board.comment.model import Comment, comment_table
from app.modules.board.comment.repository import comment_repository
from app.modules.board.comment.schema import CreateCommentRequest, UpdateCommentRequest
from app.modules.board.comment.service import comment_service
from app.modules.board.post.model import PostStatus
from app.modules.board.post.service import post_service
from tests.factories import create_board, create_comment, create_post, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')


async def _post_and_author(db: AsyncConnection, **board_overrides) -> tuple[int, int]:
    board = await create_board(db, **board_overrides)
    user = await create_user(db)
    post = await create_post(db, board_id=board.id, author_id=user.id)
    return post.id, user.id


async def _row_including_deleted(db: AsyncConnection, pk: int) -> Comment | None:
    result = await db.execute(select_rows(Comment).where(comment_table.c.id == pk))
    return one_or_none(Comment, result)


# --------------------------------------------------------------------- 작성


async def test_create_stores_the_author_from_the_principal(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)

    comment = await comment_service.create(
        db=db, post_id=post_id, actor=Principal(id=user_id), obj=CreateCommentRequest(content='첫 댓글')
    )

    assert comment.author_id == user_id
    assert comment.post_id == post_id
    assert comment.depth == 0
    assert comment.parent_id is None
    assert comment.is_removed is False


async def test_a_top_level_path_is_its_own_id(db: AsyncConnection):
    """§4.2 — 자리수를 고정해야 문자열 정렬이 곧 트리 정렬이 된다."""
    post_id, user_id = await _post_and_author(db)

    comment = await create_comment(db, post_id=post_id, author_id=user_id)

    assert comment.path == str(comment.id).zfill(8)


async def test_a_reply_extends_the_parent_path(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    parent = await create_comment(db, post_id=post_id, author_id=user_id)

    reply = await comment_service.create(
        db=db,
        post_id=post_id,
        actor=Principal(id=user_id),
        obj=CreateCommentRequest(content='답글', parent_id=parent.id),
    )

    assert reply.path == f'{parent.path}.{str(reply.id).zfill(8)}'
    assert reply.depth == 1
    assert reply.parent_id == parent.id


async def test_replying_to_a_reply_is_rejected(db: AsyncConnection):
    """§4.2 — 무한 뎁스는 화면에서 감당이 안 된다. 서버에서 막지 않으면 데이터가 먼저 망가진다."""
    post_id, user_id = await _post_and_author(db)
    parent = await create_comment(db, post_id=post_id, author_id=user_id)
    reply = await create_comment(db, post_id=post_id, author_id=user_id, parent=parent)

    with pytest.raises(BadRequestError) as caught:
        await comment_service.create(
            db=db,
            post_id=post_id,
            actor=Principal(id=user_id),
            obj=CreateCommentRequest(content='대대댓글', parent_id=reply.id),
        )

    assert caught.value.code == 'comment.too_deep'


async def test_a_parent_from_another_post_is_rejected(db: AsyncConnection):
    """부모가 다른 글의 댓글이면 트리가 두 글에 걸친다."""
    post_id, user_id = await _post_and_author(db)
    other_post_id, _ = await _post_and_author(db)
    stranger = await create_comment(db, post_id=other_post_id, author_id=user_id)

    with pytest.raises(NotFoundError) as caught:
        await comment_service.create(
            db=db,
            post_id=post_id,
            actor=Principal(id=user_id),
            obj=CreateCommentRequest(content='답글', parent_id=stranger.id),
        )

    assert caught.value.code == 'comment.parent_not_found'


async def test_commenting_on_a_missing_post_is_rejected(db: AsyncConnection):
    with pytest.raises(NotFoundError) as caught:
        await comment_service.create(
            db=db, post_id=999_999, actor=Principal(id=1), obj=CreateCommentRequest(content='댓글')
        )

    assert caught.value.code == 'post.not_found'


async def test_commenting_on_a_draft_is_rejected(db: AsyncConnection):
    board = await create_board(db)
    user = await create_user(db)
    draft = await create_post(db, board_id=board.id, author_id=user.id, status=PostStatus.draft)

    with pytest.raises(NotFoundError):
        await comment_service.create(
            db=db, post_id=draft.id, actor=Principal(id=user.id), obj=CreateCommentRequest(content='댓글')
        )


# ------------------------------------------------- comment_count (§4.4)


async def test_creating_a_comment_bumps_the_count(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)

    await comment_service.create(
        db=db, post_id=post_id, actor=Principal(id=user_id), obj=CreateCommentRequest(content='하나')
    )
    await comment_service.create(
        db=db, post_id=post_id, actor=Principal(id=user_id), obj=CreateCommentRequest(content='둘')
    )

    assert (await post_service.get(db=db, pk=post_id)).comment_count == 2


async def test_deleting_a_leaf_comment_lowers_the_count(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    comment = await comment_service.create(
        db=db, post_id=post_id, actor=Principal(id=user_id), obj=CreateCommentRequest(content='곧 지울 것')
    )

    await comment_service.delete(db=db, pk=comment.id, actor=Principal(id=user_id))

    assert (await post_service.get(db=db, pk=post_id)).comment_count == 0


async def test_a_tombstone_does_not_lower_the_count(db: AsyncConnection):
    """화면에 자리가 남아 있으면 그것은 여전히 한 개의 댓글이다.

    세는 것과 보이는 것이 어긋나면 사용자가 먼저 알아챈다.
    """
    post_id, user_id = await _post_and_author(db)
    parent = await comment_service.create(
        db=db, post_id=post_id, actor=Principal(id=user_id), obj=CreateCommentRequest(content='부모')
    )
    await comment_service.create(
        db=db,
        post_id=post_id,
        actor=Principal(id=user_id),
        obj=CreateCommentRequest(content='답글', parent_id=parent.id),
    )

    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=user_id))

    assert (await post_service.get(db=db, pk=post_id)).comment_count == 2


# --------------------------------------------------------- 트리 조회 (§4.2)


async def test_the_thread_is_ordered_by_path(db: AsyncConnection):
    """부모 바로 뒤에 그 답글이 온다 — `ORDER BY path` 한 번으로 끝난다."""
    post_id, user_id = await _post_and_author(db)
    first = await create_comment(db, post_id=post_id, author_id=user_id, content='첫째')
    await create_comment(db, post_id=post_id, author_id=user_id, parent=first, content='첫째의 답글')
    await create_comment(db, post_id=post_id, author_id=user_id, content='둘째')

    page = await comment_service.list(db=db, post_id=post_id, cursor=None, size=10)

    assert [item.content for item in page.items] == ['첫째', '첫째의 답글', '둘째']
    assert [item.depth for item in page.items] == [0, 1, 0]


async def test_the_thread_only_shows_comments_of_that_post(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    other_post_id, _ = await _post_and_author(db)
    await create_comment(db, post_id=post_id, author_id=user_id, content='우리 글')
    await create_comment(db, post_id=other_post_id, author_id=user_id, content='남의 글')

    page = await comment_service.list(db=db, post_id=post_id, cursor=None, size=10)

    assert [item.content for item in page.items] == ['우리 글']


async def test_the_thread_pages_with_a_path_cursor(db: AsyncConnection):
    """정렬 키가 곧 커서라야 keyset 이 성립한다 — `id` 로 자르면 부모와 자식이 끊긴다."""
    post_id, user_id = await _post_and_author(db)
    first = await create_comment(db, post_id=post_id, author_id=user_id, content='첫째')
    await create_comment(db, post_id=post_id, author_id=user_id, parent=first, content='첫째의 답글')
    await create_comment(db, post_id=post_id, author_id=user_id, content='둘째')

    page = await comment_service.list(db=db, post_id=post_id, cursor=None, size=2)
    assert [item.content for item in page.items] == ['첫째', '첫째의 답글']
    assert page.has_next is True
    assert page.next_cursor == first.path + '.' + str(page.items[-1].id).zfill(8)

    rest = await comment_service.list(db=db, post_id=post_id, cursor=page.next_cursor, size=2)
    assert [item.content for item in rest.items] == ['둘째']
    assert rest.has_next is False
    assert rest.next_cursor is None


async def test_deleted_comments_are_absent_from_the_thread(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    comment = await create_comment(db, post_id=post_id, author_id=user_id)
    await comment_repository.mark_deleted(db, comment.id)

    page = await comment_service.list(db=db, post_id=post_id, cursor=None, size=10)

    assert page.items == []


async def test_a_private_board_hides_the_thread(db: AsyncConnection):
    """§4.6 — 글을 볼 수 없으면 댓글도 볼 수 없다. 목록만 막고 댓글을 열면 막은 것이 아니다."""
    post_id, _ = await _post_and_author(db, read_role='member')

    with pytest.raises(UnauthorizedError):
        await comment_service.list(db=db, post_id=post_id, cursor=None, size=10)


# --------------------------------------------------------------------- 수정


async def test_the_author_can_update_their_own_comment(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    comment = await create_comment(db, post_id=post_id, author_id=user_id)

    updated = await comment_service.update(
        db=db, pk=comment.id, actor=Principal(id=user_id), obj=UpdateCommentRequest(content='고쳤다')
    )

    assert updated.content == '고쳤다'


async def test_a_stranger_cannot_update_another_persons_comment(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    comment = await create_comment(db, post_id=post_id, author_id=user_id)

    with pytest.raises(ForbiddenError) as caught:
        await comment_service.update(
            db=db, pk=comment.id, actor=Principal(id=user_id + 999), obj=UpdateCommentRequest(content='탈취')
        )

    assert caught.value.code == 'comment.not_owner'


async def test_a_tombstone_cannot_be_edited(db: AsyncConnection):
    """묘비의 내용은 이미 지워졌다. 되살리는 통로를 열어두지 않는다."""
    post_id, user_id = await _post_and_author(db)
    parent = await create_comment(db, post_id=post_id, author_id=user_id)
    await create_comment(db, post_id=post_id, author_id=user_id, parent=parent)
    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=user_id))

    with pytest.raises(ConflictError) as caught:
        await comment_service.update(
            db=db, pk=parent.id, actor=Principal(id=user_id), obj=UpdateCommentRequest(content='되살리기')
        )

    assert caught.value.code == 'comment.removed'


# ------------------------------------------------------- 삭제 (§4.7)


async def test_a_leaf_comment_is_soft_deleted(db: AsyncConnection):
    """자식이 없으면 감춘다 — `alive()` 가 처리한다."""
    post_id, user_id = await _post_and_author(db)
    comment = await create_comment(db, post_id=post_id, author_id=user_id)

    await comment_service.delete(db=db, pk=comment.id, actor=Principal(id=user_id))

    row = await _row_including_deleted(db, comment.id)
    assert row is not None
    assert row.deleted == comment.id
    assert row.is_removed is False


async def test_a_comment_with_children_becomes_a_tombstone(db: AsyncConnection):
    """§4.7 의 핵심. 감추면 대댓글이 고아가 된다."""
    post_id, user_id = await _post_and_author(db)
    parent = await create_comment(db, post_id=post_id, author_id=user_id, content='부모')
    await create_comment(db, post_id=post_id, author_id=user_id, parent=parent, content='자식')

    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=user_id))

    page = await comment_service.list(db=db, post_id=post_id, cursor=None, size=10)
    assert len(page.items) == 2  # 부모가 트리에 남아 있다
    assert page.items[0].is_removed is True
    assert page.items[1].content == '자식'  # 자식은 계속 보인다


async def test_a_tombstone_is_masked_in_the_response(db: AsyncConnection):
    """마스킹은 schema 계층이 한다 (§4.7). 행에는 원본이 남는다 — 감사·복구용이다."""
    post_id, user_id = await _post_and_author(db)
    parent = await create_comment(db, post_id=post_id, author_id=user_id, content='지워질 내용')
    await create_comment(db, post_id=post_id, author_id=user_id, parent=parent)

    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=user_id))

    page = await comment_service.list(db=db, post_id=post_id, cursor=None, size=10)
    tombstone = page.items[0]
    assert '지워질 내용' not in tombstone.content
    assert tombstone.author_id is None  # 작성자 익명화


async def test_a_tombstone_accepts_no_replies(db: AsyncConnection):
    """이미 지운 자리다. 답글을 받으면 되살아난 것처럼 보인다."""
    post_id, user_id = await _post_and_author(db)
    parent = await create_comment(db, post_id=post_id, author_id=user_id)
    await create_comment(db, post_id=post_id, author_id=user_id, parent=parent)
    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=user_id))

    with pytest.raises(BadRequestError):
        await comment_service.create(
            db=db,
            post_id=post_id,
            actor=Principal(id=user_id),
            obj=CreateCommentRequest(content='답글', parent_id=parent.id),
        )


async def test_a_stranger_cannot_delete_another_persons_comment(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    comment = await create_comment(db, post_id=post_id, author_id=user_id)

    with pytest.raises(ForbiddenError):
        await comment_service.delete(db=db, pk=comment.id, actor=Principal(id=user_id + 999))


async def test_a_superuser_can_delete_anyones_comment(db: AsyncConnection):
    post_id, user_id = await _post_and_author(db)
    comment = await create_comment(db, post_id=post_id, author_id=user_id)

    await comment_service.delete(db=db, pk=comment.id, actor=Principal(id=user_id + 999, is_superuser=True))

    assert (await comment_service.list(db=db, post_id=post_id, cursor=None, size=10)).items == []


async def test_get_raises_not_found_for_a_missing_comment(db: AsyncConnection):
    with pytest.raises(NotFoundError) as caught:
        await comment_service.get(db=db, pk=999_999)

    assert caught.value.code == 'comment.not_found'


async def test_deleting_a_tombstone_twice_does_nothing(db: AsyncConnection):
    """두 번 지워도 카운트가 두 번 줄지 않는다 — 이미 비운 자리다."""
    post_id, user_id = await _post_and_author(db)
    parent = await create_comment(db, post_id=post_id, author_id=user_id)
    await create_comment(db, post_id=post_id, author_id=user_id, parent=parent)
    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=user_id))
    before = (await post_service.get(db=db, pk=post_id)).comment_count

    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=user_id))

    assert (await post_service.get(db=db, pk=post_id)).comment_count == before


async def test_only_the_content_can_be_updated(db: AsyncConnection):
    """트리 구조를 바꾸는 것은 수정이 아니라 이동이고, 그건 별개의 동작이다."""
    assert set(UpdateCommentRequest.model_fields) == {'content'}

    with pytest.raises(ValueError, match='수정할 수 없는 컬럼'):
        await comment_repository.update(db, pk=1, changes={'parent_id': 2})
