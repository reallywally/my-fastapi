"""트랜잭션 경계를 DI 로 처리한다 (§1.1).

엔드포인트가 어떤 의존성을 선언했는지로 트랜잭션 여부가 결정된다.
`service` / `repository` 는 절대 `commit()` 하지 않는다 — 커밋은 여기서만 일어난다.

    @router.get('/{pk}')
    async def get_post(db: SessionDep, pk: int): ...        # 읽기: 트랜잭션 없음

    @router.post('')
    async def create_post(db: TxDep, obj: CreatePost): ...  # 쓰기: 자동 커밋/롤백

세션 팩토리는 `app.state` 에서 가져온다. 모듈 전역 엔진을 두지 않는 이유는 §2.1.
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, 'session_factory', None)
    if factory is None:
        # lifespan 이 돌지 않았다는 뜻이다. sys.exit() 하지 않고 예외만 올린다 (§3.3).
        raise RuntimeError('session_factory 가 app.state 에 없다 — lifespan 이 실행되지 않았다')
    return factory


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """읽기 전용 세션. 트랜잭션을 열지 않는다."""
    async with get_session_factory(request)() as session:
        yield session


async def get_db_tx(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """쓰기 세션. 정상 종료 시 커밋, 예외 시 롤백."""
    async with get_session_factory(request).begin() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]
TxDep = Annotated[AsyncSession, Depends(get_db_tx)]
