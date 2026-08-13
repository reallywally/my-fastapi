"""게시판 슬라이스의 업무 규칙을 실 DB 로 검증한다 (§4.1, §4.6).

게시판에는 주인이 없다. 소유권(`can_act_on`)이 아니라 **역할** 판정이라 관리자만
만들고 고칠 수 있다 — `actor.is_superuser` 하나로 갈린다.
"""

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import one_or_none, select_rows
from app.common.errors import ConflictError, ForbiddenError, NotFoundError
from app.common.security import Principal
from app.modules.board.board.model import Board, board_table
from app.modules.board.board.repository import board_repository
from app.modules.board.board.schema import CreateBoard, UpdateBoard
from app.modules.board.board.service import board_service
from tests.factories import board_fields, create_board

pytestmark = pytest.mark.asyncio(loop_scope='session')

ADMIN = Principal(id=1, is_superuser=True)
MEMBER = Principal(id=2)


def _new(**overrides) -> CreateBoard:
    return CreateBoard(**{'slug': 'notice', 'name': '공지사항'} | overrides)


async def _row_including_deleted(db: AsyncConnection, pk: int) -> Board | None:
    result = await db.execute(select_rows(Board).where(board_table.c.id == pk))
    return one_or_none(Board, result)


# --------------------------------------------------------------------- 생성


async def test_an_admin_can_create_a_board(db: AsyncConnection):
    board = await board_service.create(db=db, obj=_new(), actor=ADMIN)

    assert board.id > 0
    assert board.slug == 'notice'
    # 기본값은 스키마가 준다 — 게시판은 기본적으로 공개고 쓰기는 회원만이다.
    assert board.read_role == 'anonymous'
    assert board.write_role == 'member'


async def test_a_member_cannot_create_a_board(db: AsyncConnection):
    with pytest.raises(ForbiddenError) as caught:
        await board_service.create(db=db, obj=_new(), actor=MEMBER)

    assert caught.value.code == 'board.admin_only'


async def test_create_rejects_a_duplicate_slug(db: AsyncConnection):
    await create_board(db, slug='notice')

    with pytest.raises(ConflictError) as caught:
        await board_service.create(db=db, obj=_new(slug='notice'), actor=ADMIN)

    assert caught.value.code == 'board.slug_taken'


async def test_the_unique_constraint_is_the_real_guard(db: AsyncConnection):
    """사전 확인을 우회해도 DB 가 막아야 한다 — 확인과 삽입 사이에는 경합이 있다."""
    await create_board(db, slug='notice')

    with pytest.raises(IntegrityError):
        await board_repository.insert(db, **board_fields(slug='notice'))


# --------------------------------------------------------------------- 조회


async def test_get_by_slug_raises_not_found(db: AsyncConnection):
    with pytest.raises(NotFoundError) as caught:
        await board_service.get_by_slug(db=db, slug='nope')

    assert caught.value.code == 'board.not_found'


async def test_list_is_ordered_by_display_order(db: AsyncConnection):
    """목록이 곧 메뉴다. 정렬은 운영자가 정한다."""
    await create_board(db, slug='third', display_order=30)
    await create_board(db, slug='first', display_order=10)
    await create_board(db, slug='second', display_order=20)

    boards = await board_service.list(db=db)

    ordered = [board.slug for board in boards if board.slug in {'first', 'second', 'third'}]
    assert ordered == ['first', 'second', 'third']


async def test_deleted_boards_disappear_from_the_list(db: AsyncConnection):
    board = await create_board(db)
    await board_service.delete(db=db, slug=board.slug, actor=ADMIN)

    assert board.slug not in [row.slug for row in await board_service.list(db=db)]


# --------------------------------------------------------------------- 수정


async def test_an_admin_can_update_a_board(db: AsyncConnection):
    board = await create_board(db)

    updated = await board_service.update(db=db, slug=board.slug, obj=UpdateBoard(name='새 이름'), actor=ADMIN)

    assert updated.name == '새 이름'
    assert updated.slug == board.slug


async def test_a_member_cannot_update_a_board(db: AsyncConnection):
    board = await create_board(db)

    with pytest.raises(ForbiddenError) as caught:
        await board_service.update(db=db, slug=board.slug, obj=UpdateBoard(name='탈취'), actor=MEMBER)

    assert caught.value.code == 'board.admin_only'


async def test_the_slug_cannot_be_changed(db: AsyncConnection):
    """URL 식별자가 바뀌면 그 게시판을 가리키던 모든 링크가 깨진다 — 스키마에 필드가 없다."""
    assert 'slug' not in UpdateBoard.model_fields

    changes: dict[str, Any] = {'slug': 'hijacked'}
    with pytest.raises(ValueError, match='수정할 수 없는 컬럼'):
        await board_repository.update(db, pk=1, changes=changes)


# --------------------------------------------------------------------- 삭제


async def test_delete_marks_the_row_with_its_own_id(db: AsyncConnection):
    """§1.4 — hard delete 가 아니다."""
    board = await create_board(db)

    await board_service.delete(db=db, slug=board.slug, actor=ADMIN)

    row = await _row_including_deleted(db, board.id)
    assert row is not None
    assert row.deleted == board.id


async def test_a_slug_can_be_reused_after_deletion(db: AsyncConnection):
    """§1.4 를 쓰는 이유 전부. 지운 게시판의 주소를 다시 쓸 수 있다."""
    board = await create_board(db, slug='notice')
    await board_service.delete(db=db, slug='notice', actor=ADMIN)

    reborn = await board_service.create(db=db, obj=_new(slug='notice'), actor=ADMIN)

    assert reborn.id != board.id


async def test_a_member_cannot_delete_a_board(db: AsyncConnection):
    board = await create_board(db)

    with pytest.raises(ForbiddenError):
        await board_service.delete(db=db, slug=board.slug, actor=MEMBER)
