"""글 슬라이스의 업무 규칙을 실 DB 로 검증한다 (§4.3, §4.6).

두 가지가 이 파일의 핵심이다:

1. **소유권 비교는 넘겨받은 principal 로** (§4.6, 규칙 #14). FBA 는 조회한 행의 id 와
   비교해서 조건이 상수가 되었고, 권한 검사가 통째로 죽었다.
2. **커서 페이지네이션이 흔들리지 않는다** (§4.3). 페이징 중에 새 글이 들어와도
   중복·누락이 없어야 한다 — `OFFSET` 을 쓰지 않는 이유 전부다.
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import one_or_none, select_rows
from app.common.errors import ForbiddenError, NotFoundError
from app.common.security import Principal
from app.modules.board.post.model import Post, PostStatus, post_table
from app.modules.board.post.repository import post_repository
from app.modules.board.post.schema import CreatePost, UpdatePost
from app.modules.board.post.service import post_service
from tests.factories import create_board, create_post, create_posts, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')


@pytest.fixture
def author() -> Principal:
    return Principal(id=1)


async def _row_including_deleted(db: AsyncConnection, pk: int) -> Post | None:
    result = await db.execute(select_rows(Post).where(post_table.c.id == pk))
    return one_or_none(Post, result)


async def _board_and_author(db: AsyncConnection) -> tuple[int, int]:
    """FK 가 실제로 걸려 있으므로(§1.6 PRAGMA) 사용자와 게시판이 진짜로 있어야 한다."""
    board = await create_board(db)
    user = await create_user(db)
    return board.id, user.id


# --------------------------------------------------------------------- 작성


async def test_create_stores_the_author_from_the_principal(db: AsyncConnection):
    """작성자는 라우터가 넘긴 주체다. 본문에서 오지 않는다 — 오면 위조된다."""
    board_id, user_id = await _board_and_author(db)

    post = await post_service.create(
        db=db,
        board_id=board_id,
        actor=Principal(id=user_id),
        obj=CreatePost(title='제목', content='본문'),
    )

    assert post.author_id == user_id
    assert post.board_id == board_id
    assert post.status is PostStatus.published
    assert post.comment_count == 0
    assert post.view_count == 0


# --------------------------------------------------------------------- 조회


async def test_get_raises_not_found_for_a_missing_id(db: AsyncConnection):
    with pytest.raises(NotFoundError) as caught:
        await post_service.get(db=db, pk=999_999)

    assert caught.value.code == 'post.not_found'


async def test_get_raises_not_found_for_a_deleted_post(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)
    await post_repository.mark_deleted(db, post.id)

    with pytest.raises(NotFoundError):
        await post_service.get(db=db, pk=post.id)


async def test_a_draft_is_not_readable_yet(db: AsyncConnection):
    """주체가 없는 동안 초안을 열어두면 남의 초안이 공개된다. 404 는 안전한 쪽으로 틀린다."""
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id, status=PostStatus.draft)

    with pytest.raises(NotFoundError):
        await post_service.get(db=db, pk=post.id)


async def test_reading_a_post_does_not_write(db: AsyncConnection):
    """§4.5 — 읽기가 쓰기가 되면 안 된다. 조회수는 Redis 버퍼가 맡는다 (다음 항목)."""
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    for _ in range(3):
        await post_service.get(db=db, pk=post.id)

    assert (await post_service.get(db=db, pk=post.id)).view_count == 0


# --------------------------------------------------------- 목록 (§4.3)


async def test_list_returns_newest_first_without_a_total(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    await create_posts(db, 3, board_id=board_id, author_id=user_id)

    page = await post_service.list(db=db, board_id=board_id, cursor=None, size=10)

    assert [item.id for item in page.items] == sorted((item.id for item in page.items), reverse=True)
    assert page.has_next is False
    assert page.next_cursor is None


async def test_list_only_shows_posts_of_that_board(db: AsyncConnection):
    """게시판 경계가 새면 비공개 게시판의 글이 공개 게시판 목록에 나온다."""
    board_id, user_id = await _board_and_author(db)
    other = await create_board(db)
    await create_posts(db, 2, board_id=board_id, author_id=user_id)
    await create_post(db, board_id=other.id, author_id=user_id, title='남의 게시판 글')

    page = await post_service.list(db=db, board_id=board_id, cursor=None, size=10)

    assert all(item.board_id == board_id for item in page.items)
    assert '남의 게시판 글' not in [item.title for item in page.items]


async def test_list_pages_with_a_cursor(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    created = await create_posts(db, 5, board_id=board_id, author_id=user_id)
    newest_first = sorted((post.id for post in created), reverse=True)

    first = await post_service.list(db=db, board_id=board_id, cursor=None, size=2)
    assert [item.id for item in first.items] == newest_first[:2]
    assert first.has_next is True
    assert first.next_cursor == newest_first[1]

    second = await post_service.list(db=db, board_id=board_id, cursor=first.next_cursor, size=2)
    assert [item.id for item in second.items] == newest_first[2:4]


async def test_a_post_written_mid_paging_does_not_duplicate_or_skip(db: AsyncConnection):
    """§4.3 을 쓰는 이유 전부. 커서는 id 라서 새 글이 앞에 끼어도 흔들리지 않는다."""
    board_id, user_id = await _board_and_author(db)
    created = await create_posts(db, 4, board_id=board_id, author_id=user_id)
    newest_first = sorted((post.id for post in created), reverse=True)

    first = await post_service.list(db=db, board_id=board_id, cursor=None, size=2)
    await create_post(db, board_id=board_id, author_id=user_id)  # 페이지를 넘기는 사이에 새 글
    second = await post_service.list(db=db, board_id=board_id, cursor=first.next_cursor, size=2)

    seen = [item.id for item in first.items] + [item.id for item in second.items]
    assert seen == newest_first
    assert len(seen) == len(set(seen))


async def test_drafts_are_absent_from_the_list(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    await create_posts(db, 2, board_id=board_id, author_id=user_id)
    draft = await create_post(db, board_id=board_id, author_id=user_id, status=PostStatus.draft)

    page = await post_service.list(db=db, board_id=board_id, cursor=None, size=10)

    assert draft.id not in [item.id for item in page.items]


async def test_deleted_posts_are_absent_from_the_list(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    posts = await create_posts(db, 3, board_id=board_id, author_id=user_id)
    await post_repository.mark_deleted(db, posts[0].id)

    page = await post_service.list(db=db, board_id=board_id, cursor=None, size=10)

    assert posts[0].id not in [item.id for item in page.items]


# ------------------------------------------------------- 고정글 (§4.3)


async def test_pinned_posts_come_first_on_the_first_page(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    await create_posts(db, 3, board_id=board_id, author_id=user_id)
    pinned = await create_post(db, board_id=board_id, author_id=user_id, is_pinned=True, title='공지')

    page = await post_service.list(db=db, board_id=board_id, cursor=None, size=10)

    assert page.items[0].id == pinned.id
    assert page.items[0].is_pinned is True


async def test_pinned_posts_do_not_repeat_on_later_pages(db: AsyncConnection):
    """매 페이지에 붙이면 스크롤할 때마다 같은 글이 반복된다."""
    board_id, user_id = await _board_and_author(db)
    await create_posts(db, 4, board_id=board_id, author_id=user_id)
    pinned = await create_post(db, board_id=board_id, author_id=user_id, is_pinned=True)

    first = await post_service.list(db=db, board_id=board_id, cursor=None, size=2)
    second = await post_service.list(db=db, board_id=board_id, cursor=first.next_cursor, size=2)

    assert pinned.id in [item.id for item in first.items]
    assert pinned.id not in [item.id for item in second.items]


async def test_pinned_posts_do_not_affect_the_cursor(db: AsyncConnection):
    """정렬 키에 `is_pinned` 를 섞으면 커서가 깨진다. 커서는 고정글이 아닌 쪽에서만 나온다."""
    board_id, user_id = await _board_and_author(db)
    regular = await create_posts(db, 3, board_id=board_id, author_id=user_id)
    await create_post(db, board_id=board_id, author_id=user_id, is_pinned=True)

    page = await post_service.list(db=db, board_id=board_id, cursor=None, size=2)

    newest_regular = sorted((post.id for post in regular), reverse=True)
    assert page.next_cursor == newest_regular[1]
    # 고정글 1 + 일반글 2 = 3개가 실리지만 커서는 일반글 기준이다
    assert len(page.items) == 3


# --------------------------------------------------------------------- 수정


async def test_the_author_can_update_their_own_post(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    updated = await post_service.update(
        db=db, pk=post.id, actor=Principal(id=user_id), obj=UpdatePost(title='고친 제목')
    )

    assert updated.title == '고친 제목'
    assert updated.content == post.content
    assert updated.updated_at >= post.updated_at


async def test_a_stranger_cannot_update_another_persons_post(db: AsyncConnection):
    """§4.6 / 규칙 #14 — 비교 대상은 넘겨받은 principal 이다.

    FBA 는 조회한 행의 id 와 비교해서 조건이 항상 참이 되었다.
    """
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    with pytest.raises(ForbiddenError) as caught:
        await post_service.update(db=db, pk=post.id, actor=Principal(id=user_id + 999), obj=UpdatePost(title='탈취'))

    assert caught.value.code == 'post.not_owner'


async def test_a_superuser_can_update_anyones_post(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    updated = await post_service.update(
        db=db,
        pk=post.id,
        actor=Principal(id=user_id + 999, is_superuser=True),
        obj=UpdatePost(title='관리자 수정'),
    )

    assert updated.title == '관리자 수정'


async def test_update_leaves_omitted_fields_alone(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    updated = await post_service.update(
        db=db, pk=post.id, actor=Principal(id=user_id), obj=UpdatePost(content='본문만 교체')
    )

    assert updated.content == '본문만 교체'
    assert updated.title == post.title


async def test_the_board_cannot_be_changed(db: AsyncConnection):
    """글을 다른 게시판으로 옮기면 권한 판정이 달라진다 (§4.6). 스키마에 필드가 없다."""
    assert 'board_id' not in UpdatePost.model_fields

    changes: dict[str, Any] = {'board_id': 2}
    with pytest.raises(ValueError, match='수정할 수 없는 컬럼'):
        await post_repository.update(db, pk=1, changes=changes)


# --------------------------------------------------------------------- 삭제


async def test_delete_marks_the_row_with_its_own_id(db: AsyncConnection):
    """§1.4 — hard delete 가 아니다."""
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    await post_service.delete(db=db, pk=post.id, actor=Principal(id=user_id))

    row = await _row_including_deleted(db, post.id)
    assert row is not None
    assert row.deleted == post.id


async def test_a_stranger_cannot_delete_another_persons_post(db: AsyncConnection):
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    with pytest.raises(ForbiddenError):
        await post_service.delete(db=db, pk=post.id, actor=Principal(id=user_id + 999))


# ------------------------------------------------- comment_count (§4.4)


async def test_comment_count_is_bumped_in_the_database(db: AsyncConnection):
    """§4.4 — 앱에서 읽고 더해서 쓰면 동시 댓글에서 갱신이 유실된다.

    댓글 모듈은 다음 항목이지만 쿼리는 `post` 가 소유한다 (§4.1 의 의존 방향).
    """
    board_id, user_id = await _board_and_author(db)
    post = await create_post(db, board_id=board_id, author_id=user_id)

    await post_repository.bump_comment_count(db, post.id, 1)
    await post_repository.bump_comment_count(db, post.id, 1)
    await post_repository.bump_comment_count(db, post.id, -1)

    assert (await post_service.get(db=db, pk=post.id)).comment_count == 1
