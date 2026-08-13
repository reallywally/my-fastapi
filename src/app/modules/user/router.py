"""HTTP 만 다룬다 (§1.2). 검증·직렬화·상태코드.

트랜잭션은 **여기서 선언한 의존성으로 결정된다** (§1.1):
- 읽기 → `ConnDep` (트랜잭션은 열되 끝나면 롤백)
- 쓰기 → `TxDep` (자동 커밋/롤백)

서비스에는 원시 타입과 스키마만 넘긴다. `Request` 를 그대로 흘리지 않는다 (§2.7).
"""

from fastapi import APIRouter, status

from app.common.db import ConnDep, TxDep
from app.common.pagination import CursorDep, Page
from app.modules.user.deps import PrincipalDep
from app.modules.user.schema import CreateUser, UpdateUser, UserOut
from app.modules.user.service import user_service

router = APIRouter(prefix='/users', tags=['user'])


@router.post('', status_code=status.HTTP_201_CREATED, summary='가입')
async def create_user(db: TxDep, obj: CreateUser) -> UserOut:
    user = await user_service.create(db=db, obj=obj)
    return UserOut.model_validate(user)


@router.get('', summary='목록 (커서 페이지네이션)')
async def list_users(db: ConnDep, page: CursorDep) -> Page[UserOut]:
    return await user_service.list(db=db, cursor=page.cursor, size=page.size)


@router.get('/{pk}', summary='상세')
async def get_user(db: ConnDep, pk: int) -> UserOut:
    user = await user_service.get(db=db, pk=pk)
    return UserOut.model_validate(user)


@router.patch('/{pk}', summary='수정 (본인 또는 관리자)')
async def update_user(db: TxDep, pk: int, obj: UpdateUser, actor: PrincipalDep) -> UserOut:
    user = await user_service.update(db=db, pk=pk, actor=actor, obj=obj)
    return UserOut.model_validate(user)


@router.delete('/{pk}', status_code=status.HTTP_204_NO_CONTENT, summary='탈퇴 (본인 또는 관리자)')
async def delete_user(db: TxDep, pk: int, actor: PrincipalDep) -> None:
    await user_service.delete(db=db, pk=pk, actor=actor)
