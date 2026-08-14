"""첨부 슬라이스의 업무 규칙을 실 DB 로 검증한다 (§4.9).

핵심은 세 가지다:

1. **권한은 글의 소유권을 따른다.** 남의 글에 파일을 붙일 수 없다 (규칙 #14)
2. **읽기 권한이 다운로드에도 걸린다.** 글은 막고 첨부는 여는 것은 막은 것이 아니다
3. **행을 지워도 파일은 남는다.** 파일 삭제는 롤백되지 않아서 트랜잭션 안에 둘 수 없다
"""

import pytest

from app.common.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.common.security import Principal
from app.modules.board.attachment.repository import attachment_repository
from app.modules.board.attachment.service import attachment_service
from app.modules.board.post.model import PostStatus
from tests.factories import create_attachment, create_board, create_post, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')


@pytest.fixture
async def author(db):
    return await create_user(db)


@pytest.fixture
async def post(db, author):
    board = await create_board(db)
    return await create_post(db, board_id=board.id, author_id=author.id)


def _principal(user_id: int, *, superuser: bool = False) -> Principal:
    return Principal(id=user_id, is_superuser=superuser)


# --------------------------------------------------------------- 업로드 권한


async def test_the_post_author_may_attach(db, post, author):
    board_post = await attachment_service.assert_can_attach(db=db, post_id=post.id, actor=_principal(author.id))

    assert board_post.id == post.id


async def test_a_stranger_may_not_attach(db, post):
    """§4.10 의 `[본인]`. 남의 글에 파일을 붙일 수 있으면 그건 남의 글이 아니다."""
    stranger = await create_user(db)

    with pytest.raises(ForbiddenError) as exc:
        await attachment_service.assert_can_attach(db=db, post_id=post.id, actor=_principal(stranger.id))

    assert exc.value.code == 'post.not_owner'


async def test_an_admin_may_attach(db, post):
    admin = await create_user(db)

    board_post = await attachment_service.assert_can_attach(
        db=db, post_id=post.id, actor=_principal(admin.id, superuser=True)
    )

    assert board_post.id == post.id


async def test_a_board_that_forbids_attachments_refuses(db, author):
    board = await create_board(db, allow_attachment=False)
    post = await create_post(db, board_id=board.id, author_id=author.id)

    with pytest.raises(ForbiddenError) as exc:
        await attachment_service.assert_can_attach(db=db, post_id=post.id, actor=_principal(author.id))

    assert exc.value.code == 'attachment.not_allowed'


async def test_attaching_to_a_missing_post_is_404(db, author):
    with pytest.raises(NotFoundError) as exc:
        await attachment_service.assert_can_attach(db=db, post_id=999999, actor=_principal(author.id))

    assert exc.value.code == 'post.not_found'


async def test_attaching_to_a_draft_is_404(db, author):
    """초안은 없는 것으로 취급한다 — 주체로 판정하려면 Phase 5 가 필요하다."""
    board = await create_board(db)
    draft = await create_post(db, board_id=board.id, author_id=author.id, status=PostStatus.draft)

    with pytest.raises(NotFoundError):
        await attachment_service.assert_can_attach(db=db, post_id=draft.id, actor=_principal(author.id))


# ------------------------------------------------------------------ 붙이기


async def test_attach_stores_only_primitives(db, post, author):
    """서비스에 넘어가는 것은 전부 원시 타입이다 (§2.7, 규칙 #5)."""
    attachment = await attachment_service.attach(
        db=db,
        post_id=post.id,
        actor=_principal(author.id),
        filename='보고서.pdf',
        content_type='application/pdf',
        size=1234,
        storage_key='2026/08/' + 'a' * 32 + '.pdf',
    )

    assert attachment.post_id == post.id
    assert attachment.author_id == author.id
    assert attachment.filename == '보고서.pdf'


# ------------------------------------------------------------------ 조회


async def test_listing_returns_upload_order(db, post, author):
    first = await create_attachment(db, author_id=author.id, post_id=post.id)
    second = await create_attachment(db, author_id=author.id, post_id=post.id)

    rows = await attachment_service.list(db=db, post_id=post.id)

    assert [row.id for row in rows] == [first.id, second.id]


async def test_a_non_public_board_hides_its_attachments(db, author):
    """§4.6 — 읽기 권한이 첨부에도 걸린다."""
    board = await create_board(db, read_role='member')
    post = await create_post(db, board_id=board.id, author_id=author.id)
    attachment = await create_attachment(db, author_id=author.id, post_id=post.id)

    with pytest.raises(UnauthorizedError) as exc:
        await attachment_service.get_for_download(db=db, pk=attachment.id, actor=None)

    assert exc.value.code == 'auth.unauthorized'


async def test_downloading_a_missing_attachment_is_404(db):
    with pytest.raises(NotFoundError) as exc:
        await attachment_service.get_for_download(db=db, pk=999999, actor=None)

    assert exc.value.code == 'attachment.not_found'


async def test_an_unattached_file_is_visible_only_to_its_uploader(db, author):
    """§4.9 — nullable `post_id`. 판정할 게시판이 없으면 본인만 받는다."""
    attachment = await create_attachment(db, author_id=author.id)

    mine = await attachment_service.get_for_download(db=db, pk=attachment.id, actor=_principal(author.id))
    assert mine.id == attachment.id

    with pytest.raises(NotFoundError):
        await attachment_service.get_for_download(db=db, pk=attachment.id, actor=None)


# ------------------------------------------------------------------ 삭제


async def test_delete_removes_the_row_but_not_the_key(db, post, author):
    """행만 지운다. 파일은 정리 배치가 지운다 (§4.9)."""
    attachment = await create_attachment(db, author_id=author.id, post_id=post.id)

    await attachment_service.delete(db=db, pk=attachment.id, actor=_principal(author.id))

    assert await attachment_repository.get(db, attachment.id) is None
    # 글에 붙어 있던 파일이라 정리 배치가 건드리지 않는다 — 복구를 전제한다 (§1.4).
    assert attachment.storage_key in await attachment_repository.protected_keys(db)


async def test_a_stranger_may_not_delete(db, post, author):
    attachment = await create_attachment(db, author_id=author.id, post_id=post.id)
    stranger = await create_user(db)

    with pytest.raises(ForbiddenError) as exc:
        await attachment_service.delete(db=db, pk=attachment.id, actor=_principal(stranger.id))

    assert exc.value.code == 'attachment.not_owner'
