"""정합성 배치 (§4.4, §4.9).

**배치 자체가 테스트 대상이다.** 야간에 조용히 도는 코드라, 틀리면 아무도 모르는 채로
데이터가 어긋난다 — 카운트를 잘못 고치는 배치는 카운트가 어긋난 것보다 나쁘다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.common.security import Principal
from app.modules.board.attachment.maintenance import sweep_unattached_rows, sweep_unknown_files
from app.modules.board.attachment.repository import attachment_repository
from app.modules.board.attachment.service import attachment_service
from app.modules.board.comment.maintenance import reconcile_comment_counts
from app.modules.board.comment.schema import CreateCommentRequest
from app.modules.board.comment.service import comment_service
from app.modules.board.post.repository import post_repository
from tests.factories import create_attachment, create_board, create_comment, create_post, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')


async def _post(db):
    board = await create_board(db)
    user = await create_user(db)
    return await create_post(db, board_id=board.id, author_id=user.id), user


# --------------------------------------------------- comment_count 보정 (§4.4)


async def test_a_correct_count_is_left_alone(db):
    """고칠 것이 없으면 아무것도 쓰지 않는다."""
    post, user = await _post(db)
    await comment_service.create(
        db=db, post_id=post.id, actor=Principal(id=user.id), obj=CreateCommentRequest(content='댓글')
    )

    result = await reconcile_comment_counts(db)

    assert result.repaired == 0
    assert (await post_repository.get(db, post.id)).comment_count == 1


async def test_drift_upward_is_repaired(db):
    """카운트가 실제보다 많다 — 댓글이 트랜잭션 밖에서 사라진 경우다."""
    post, user = await _post(db)
    await create_comment(db, post_id=post.id, author_id=user.id)
    await post_repository.bump_comment_count(db, post.id, 10)

    result = await reconcile_comment_counts(db)

    assert result.repaired == 1
    assert (await post_repository.get(db, post.id)).comment_count == 1


async def test_drift_downward_is_repaired(db):
    post, user = await _post(db)
    await create_comment(db, post_id=post.id, author_id=user.id)
    await create_comment(db, post_id=post.id, author_id=user.id)
    await post_repository.set_comment_count(db, post.id, 0)

    await reconcile_comment_counts(db)

    assert (await post_repository.get(db, post.id)).comment_count == 2


async def test_a_tombstone_still_counts(db):
    """§4.7 — 화면에 자리가 남아 있으면 그것은 여전히 한 개의 댓글이다.

    배치가 묘비를 빼고 세면, 돌 때마다 화면의 개수가 조용히 줄어든다.
    """
    post, user = await _post(db)
    actor = Principal(id=user.id)
    parent = await comment_service.create(db=db, post_id=post.id, actor=actor, obj=CreateCommentRequest(content='부모'))
    await comment_service.create(
        db=db, post_id=post.id, actor=actor, obj=CreateCommentRequest(content='답글', parent_id=parent.id)
    )
    # 자식이 있는 댓글의 삭제는 묘비다. 카운트는 줄지 않는다 (§4.7).
    await comment_service.delete(db=db, pk=parent.id, actor=actor)
    assert (await post_repository.get(db, post.id)).comment_count == 2

    result = await reconcile_comment_counts(db)

    assert result.repaired == 0
    assert (await post_repository.get(db, post.id)).comment_count == 2


async def test_deleted_comments_do_not_count(db):
    post, user = await _post(db)
    comment = await create_comment(db, post_id=post.id, author_id=user.id)
    await post_repository.bump_comment_count(db, post.id, 1)
    await comment_service.delete(db=db, pk=comment.id, actor=Principal(id=user.id))
    await post_repository.set_comment_count(db, post.id, 5)  # 드리프트를 만든다

    await reconcile_comment_counts(db)

    assert (await post_repository.get(db, post.id)).comment_count == 0


async def test_reconcile_walks_every_page(db):
    """한 번에 다 하지 않는다 — 작은 batch_size 에서도 전부 훑어야 한다."""
    board = await create_board(db)
    user = await create_user(db)
    posts = [await create_post(db, board_id=board.id, author_id=user.id) for _ in range(5)]
    for post in posts:
        await post_repository.set_comment_count(db, post.id, 7)

    result = await reconcile_comment_counts(db, batch_size=2)

    assert result.repaired == 5
    for post in posts:
        assert (await post_repository.get(db, post.id)).comment_count == 0


async def test_reconcile_respects_its_limit(db):
    """게시글이 수백만 개면 한 번에 다 도는 것이 곧 밤새 도는 것이다."""
    board = await create_board(db)
    user = await create_user(db)
    for _ in range(5):
        await create_post(db, board_id=board.id, author_id=user.id)

    result = await reconcile_comment_counts(db, batch_size=2, limit=3)

    assert result.scanned == 3


# ------------------------------------------------------ 고아 첨부 정리 (§4.9)


async def test_an_unattached_row_past_its_ttl_is_removed(db):
    _, user = await _post(db)
    orphan = await create_attachment(db, author_id=user.id)

    removed = await sweep_unattached_rows(db, older_than=timedelta(hours=-1))

    assert removed == 1
    assert await attachment_repository.get(db, orphan.id) is None


async def test_a_fresh_unattached_row_is_left_alone(db):
    """방금 올라온 미연결 파일은 고아가 아니라 **진행 중**이다."""
    _, user = await _post(db)
    fresh = await create_attachment(db, author_id=user.id)

    removed = await sweep_unattached_rows(db, older_than=timedelta(days=1))

    assert removed == 0
    assert await attachment_repository.get(db, fresh.id) is not None


async def test_an_attached_row_is_never_swept(db):
    post, user = await _post(db)
    attached = await create_attachment(db, author_id=user.id, post_id=post.id)

    await sweep_unattached_rows(db, older_than=timedelta(hours=-1))

    assert await attachment_repository.get(db, attached.id) is not None


async def test_a_file_no_row_knows_about_is_deleted(db, storage):
    """업로드는 됐는데 행 삽입이 실패한 자국. 배치가 없으면 디스크가 조용히 찬다."""
    await storage.clear()

    async def _bytes():
        yield b'orphan'

    await storage.save(_bytes(), suffix='.txt', max_bytes=1024)

    deleted = await sweep_unknown_files(db, storage, older_than=timedelta(hours=-1))

    assert deleted == 1
    assert await storage.keys() == []


async def test_a_file_of_a_live_row_is_kept(db, storage):
    await storage.clear()
    post, user = await _post(db)

    async def _bytes():
        yield b'kept'

    stored = await storage.save(_bytes(), suffix='.txt', max_bytes=1024)
    await attachment_service.attach(
        db=db,
        post_id=post.id,
        actor=Principal(id=user.id),
        filename='kept.txt',
        content_type='text/plain',
        size=stored.size,
        storage_key=stored.key,
    )

    deleted = await sweep_unknown_files(db, storage, older_than=timedelta(hours=-1))

    assert deleted == 0
    assert await storage.keys() == [stored.key]


async def test_a_file_of_a_soft_deleted_attachment_is_kept(db, storage):
    """§1.4 — soft delete 는 복구를 전제한다. 파일까지 없애면 빈 껍데기가 돌아온다."""
    await storage.clear()
    post, user = await _post(db)

    async def _bytes():
        yield b'recoverable'

    stored = await storage.save(_bytes(), suffix='.txt', max_bytes=1024)
    attachment = await attachment_service.attach(
        db=db,
        post_id=post.id,
        actor=Principal(id=user.id),
        filename='x.txt',
        content_type='text/plain',
        size=stored.size,
        storage_key=stored.key,
    )
    await attachment_service.delete(db=db, pk=attachment.id, actor=Principal(id=user.id))

    deleted = await sweep_unknown_files(db, storage, older_than=timedelta(hours=-1))

    assert deleted == 0
    assert await storage.keys() == [stored.key]


async def test_a_freshly_written_file_is_never_swept(db, storage):
    """같은 요청이 아직 진행 중일 수 있다 — 지우면 우리가 그 업로드를 깨뜨린다."""
    await storage.clear()

    async def _bytes():
        yield b'in-flight'

    await storage.save(_bytes(), suffix='.txt', max_bytes=1024)

    deleted = await sweep_unknown_files(db, storage, older_than=timedelta(days=1), now=datetime.now(UTC))

    assert deleted == 0
    assert len(await storage.keys()) == 1
