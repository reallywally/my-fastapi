"""업무 규칙 (§1.2). stateless + 모듈 전역 인스턴스 (§1.3).

지키는 것:
- **`commit()` 하지 않는다** (§1.1, 규칙 #1). 예외를 올리면 DI 가 롤백한다.
- **`Request` 를 받지 않는다** (§2.7, 규칙 #5). 필요한 값은 라우터가 원시 타입으로 넘긴다.
- **에러는 코드로 raise 한다** (§2.6, 규칙 #7). 메시지는 카탈로그가 갖는다.
- **소유권 비교는 넘겨받은 principal 로** 한다 (§4.6, 규칙 #14). 조회한 행의 id 와
  비교하면 조건이 상수가 되어 검사가 통째로 죽는다 — FBA 의 실제 버그다.

ORM 이 없으므로 **행 객체를 고쳐도 DB 는 모른다.** 수정은 반드시 레포지토리를 거치고,
갱신된 행은 다시 읽어서 받는다. unit of work 를 잃은 대가이자, 동시에 "언제 SQL 이
나가는지가 코드에 보인다" 는 이득이다 (§1.6).
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.errors import ConflictError, ForbiddenError, NotFoundError
from app.common.pagination import Page
from app.common.security import Principal, hash_password
from app.modules.user.model import User
from app.modules.user.repository import user_repository
from app.modules.user.schema import CreateUserRequest, UpdateUserRequest, UserResponse


class UserService:
    @staticmethod
    async def create(*, db: AsyncConnection, obj: CreateUserRequest) -> User:
        """가입.

        중복을 미리 확인하는 것은 **어느 필드가 겹쳤는지 알려주기 위해서**다.
        정합성을 지키는 것은 DB 의 unique 제약이다 — 확인과 삽입 사이에 경합이 있고,
        거기서 진 쪽은 `IntegrityError` 로 돌아온다. 둘 다 필요하다.
        """
        if await user_repository.get_by_username(db, obj.username) is not None:
            raise ConflictError(code='user.username_taken')
        if await user_repository.get_by_email(db, obj.email) is not None:
            raise ConflictError(code='user.email_taken')

        try:
            return await user_repository.insert(
                db,
                username=obj.username,
                email=obj.email,
                nickname=obj.nickname,
                password_hash=hash_password(obj.password),
            )
        except IntegrityError as exc:
            # 경합에서 진 경우. 어느 쪽이 겹쳤는지는 알 수 없으므로 뭉뚱그린다.
            raise ConflictError(code='user.already_exists') from exc

    @staticmethod
    async def get(*, db: AsyncConnection, pk: int) -> User:
        user = await user_repository.get(db, pk)
        if user is None:
            raise NotFoundError(code='user.not_found')
        return user

    @classmethod
    async def list(cls, *, db: AsyncConnection, cursor: int | None, size: int) -> Page[UserResponse]:
        rows = await user_repository.list_page(db, cursor=cursor, size=size)
        return Page[UserResponse].of(
            rows,
            size=size,
            cursor_of=lambda row: row.id,
            to_item=UserResponse.model_validate,
        )

    @classmethod
    async def update(cls, *, db: AsyncConnection, pk: int, actor: Principal, obj: UpdateUserRequest) -> User:
        user = await cls.get(db=db, pk=pk)
        if not actor.can_act_on(user.id):
            raise ForbiddenError(code='user.not_owner')

        changes = obj.changes()
        if 'email' in changes and changes['email'] != user.email:
            existing = await user_repository.get_by_email(db, str(changes['email']))
            if existing is not None:
                raise ConflictError(code='user.email_taken')

        try:
            updated = await user_repository.update(db, pk=user.id, changes=changes)
        except IntegrityError as exc:
            raise ConflictError(code='user.already_exists') from exc
        if updated is None:  # pragma: no cover - 같은 트랜잭션 안에서 방금 조회한 행이 사라질 수는 없다
            raise NotFoundError(code='user.not_found')
        return updated

    @classmethod
    async def delete(cls, *, db: AsyncConnection, pk: int, actor: Principal) -> None:
        """탈퇴. hard delete 가 아니라 `deleted = id` (§1.4).

        아이디·이메일이 풀려서 재가입이 가능해진다.
        """
        user = await cls.get(db=db, pk=pk)
        if not actor.can_act_on(user.id):
            raise ForbiddenError(code='user.not_owner')
        await user_repository.mark_deleted(db, user.id)


user_service = UserService()
