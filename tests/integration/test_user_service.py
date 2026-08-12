"""사용자 모듈의 업무 규칙을 실 DB 로 검증한다 (§1.2, Phase 3).

repository 는 쿼리만, service 는 규칙만 — 이 테스트가 그 경계를 확인한다.
`actor` 는 라우터가 넘기는 값이라 여기서는 직접 만든다 (§2.7: 서비스는 HTTP 를 모른다).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import ConflictError, ForbiddenError, NotFoundError
from app.common.security import Principal, verify_password
from app.modules.user.model import User
from app.modules.user.repository import user_repository
from app.modules.user.schema import CreateUser, UpdateUser
from app.modules.user.service import user_service
from tests.factories import DEFAULT_PASSWORD, build_user, create_user, create_users

pytestmark = pytest.mark.asyncio(loop_scope='session')


def _new(**overrides) -> CreateUser:
    return CreateUser(
        **{
            'username': 'gildong',
            'email': 'gildong@example.com',
            'nickname': '홍길동',
            'password': DEFAULT_PASSWORD,
        }
        | overrides
    )


# --------------------------------------------------------------------- 가입


async def test_create_stores_a_hash_not_the_plaintext(db: AsyncSession):
    user = await user_service.create(db=db, obj=_new())

    assert user.id is not None
    assert user.password_hash != DEFAULT_PASSWORD
    assert verify_password(DEFAULT_PASSWORD, user.password_hash)


async def test_create_rejects_a_duplicate_username(db: AsyncSession):
    await create_user(db, username='gildong')

    with pytest.raises(ConflictError) as caught:
        await user_service.create(db=db, obj=_new(username='gildong'))

    assert caught.value.code == 'user.username_taken'


async def test_create_rejects_a_duplicate_email(db: AsyncSession):
    await create_user(db, email='taken@example.com')

    with pytest.raises(ConflictError) as caught:
        await user_service.create(db=db, obj=_new(email='taken@example.com'))

    assert caught.value.code == 'user.email_taken'


async def test_the_unique_constraint_is_the_real_guard(db: AsyncSession):
    """사전 확인을 우회해도 DB 가 막아야 한다 — 확인과 삽입 사이에는 경합이 있다."""
    await create_user(db, username='gildong')

    # 서비스의 사전 확인을 건너뛰고 레포지토리로 직접 넣는다.
    with pytest.raises(IntegrityError):
        await user_repository.insert(db, build_user(username='gildong'))


# ------------------------------------------------------------------- 조회


async def test_get_raises_not_found_for_a_missing_id(db: AsyncSession):
    with pytest.raises(NotFoundError) as caught:
        await user_service.get(db=db, pk=999_999)

    assert caught.value.code == 'user.not_found'


async def test_get_raises_not_found_for_a_deleted_user(db: AsyncSession):
    """§2.4 — 서비스는 `deleted == 0` 을 쓰지 않는다. 전역 필터가 처리한다."""
    user = await create_user(db)
    user_id = user.id
    await user_repository.mark_deleted(db, user_id)
    db.expunge_all()

    with pytest.raises(NotFoundError):
        await user_service.get(db=db, pk=user_id)


# --------------------------------------------------------- 목록 (§4.3)


async def test_list_returns_newest_first_without_a_total(db: AsyncSession):
    await create_users(db, 3)

    page = await user_service.list(db=db, cursor=None, size=10)

    assert [item.id for item in page.items] == sorted((item.id for item in page.items), reverse=True)
    assert page.has_next is False
    assert page.next_cursor is None


async def test_list_pages_with_a_cursor(db: AsyncSession):
    created = await create_users(db, 5)
    newest_first = sorted((user.id for user in created), reverse=True)

    first = await user_service.list(db=db, cursor=None, size=2)
    assert [item.id for item in first.items] == newest_first[:2]
    assert first.has_next is True
    assert first.next_cursor == newest_first[1]

    second = await user_service.list(db=db, cursor=first.next_cursor, size=2)
    assert [item.id for item in second.items] == newest_first[2:4]


async def test_a_row_inserted_mid_paging_does_not_duplicate_or_skip(db: AsyncSession):
    """OFFSET 을 쓰지 않는 이유 (§4.3). 커서는 id 라서 새 행이 앞에 끼어도 흔들리지 않는다."""
    created = await create_users(db, 4)
    newest_first = sorted((user.id for user in created), reverse=True)

    first = await user_service.list(db=db, cursor=None, size=2)
    await create_user(db)  # 페이지를 넘기는 사이에 가입이 일어난다
    second = await user_service.list(db=db, cursor=first.next_cursor, size=2)

    seen = [item.id for item in first.items] + [item.id for item in second.items]
    assert seen == newest_first
    assert len(seen) == len(set(seen))


async def test_deleted_users_are_absent_from_the_list(db: AsyncSession):
    users = await create_users(db, 3)
    await user_repository.mark_deleted(db, users[0].id)
    db.expunge_all()

    page = await user_service.list(db=db, cursor=None, size=10)

    assert users[0].id not in [item.id for item in page.items]


# ------------------------------------------------------------------- 수정


async def test_owner_can_update_their_own_nickname(db: AsyncSession):
    user = await create_user(db)

    updated = await user_service.update(
        db=db, pk=user.id, actor=Principal(id=user.id), obj=UpdateUser(nickname='새이름')
    )

    assert updated.nickname == '새이름'


async def test_a_stranger_cannot_update_another_account(db: AsyncSession):
    """§4.6 / 규칙 #14 — 비교 대상은 넘겨받은 principal 이다.

    FBA 는 조회한 행의 id 와 비교해서 조건이 항상 참이 되었고, 관리자가 타인의 설정을
    바꾸면 **본인 값**이 뒤집혔다.
    """
    owner = await create_user(db)
    stranger = await create_user(db)

    with pytest.raises(ForbiddenError) as caught:
        await user_service.update(db=db, pk=owner.id, actor=Principal(id=stranger.id), obj=UpdateUser(nickname='탈취'))

    assert caught.value.code == 'user.not_owner'


async def test_a_superuser_can_update_anyone(db: AsyncSession):
    owner = await create_user(db)

    updated = await user_service.update(
        db=db,
        pk=owner.id,
        actor=Principal(id=owner.id + 1000, is_superuser=True),
        obj=UpdateUser(nickname='관리자수정'),
    )

    assert updated.nickname == '관리자수정'


async def test_update_rejects_an_email_owned_by_someone_else(db: AsyncSession):
    owner = await create_user(db)
    other = await create_user(db, email='taken@example.com')

    with pytest.raises(ConflictError) as caught:
        await user_service.update(db=db, pk=owner.id, actor=Principal(id=owner.id), obj=UpdateUser(email=other.email))

    assert caught.value.code == 'user.email_taken'


async def test_update_accepts_the_users_own_email_unchanged(db: AsyncSession):
    """자기 이메일을 그대로 다시 보내는 것은 충돌이 아니다."""
    user = await create_user(db)

    updated = await user_service.update(
        db=db, pk=user.id, actor=Principal(id=user.id), obj=UpdateUser(email=user.email, nickname='그대로')
    )

    assert updated.nickname == '그대로'


async def test_update_leaves_omitted_fields_alone(db: AsyncSession):
    user = await create_user(db)
    original_email = user.email

    await user_service.update(db=db, pk=user.id, actor=Principal(id=user.id), obj=UpdateUser(nickname='변경'))

    assert user.email == original_email


# ------------------------------------------------------------------- 탈퇴


async def test_delete_marks_the_row_with_its_own_id(db: AsyncSession):
    """§1.4 — hard delete 가 아니다."""
    user = await create_user(db)
    user_id = user.id

    await user_service.delete(db=db, pk=user_id, actor=Principal(id=user_id))
    db.expunge_all()

    row = (
        await db.execute(select(User).where(User.id == user_id).execution_options(include_deleted=True))
    ).scalar_one()
    assert row.deleted == user_id


async def test_username_is_reusable_after_deletion(db: AsyncSession):
    """§1.4 를 쓰는 이유 전부. 탈퇴한 아이디로 재가입이 된다."""
    user = await create_user(db, username='gildong', email='gildong@example.com')
    await user_service.delete(db=db, pk=user.id, actor=Principal(id=user.id))
    db.expunge_all()

    reborn = await user_service.create(db=db, obj=_new(username='gildong'))

    assert reborn.id != user.id


async def test_a_stranger_cannot_delete_another_account(db: AsyncSession):
    owner = await create_user(db)
    stranger = await create_user(db)

    with pytest.raises(ForbiddenError):
        await user_service.delete(db=db, pk=owner.id, actor=Principal(id=stranger.id))
