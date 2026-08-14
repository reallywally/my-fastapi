"""트랜잭션 경계를 DI 로 처리한다 (§1.1).

엔드포인트가 어떤 의존성을 선언했는지로 트랜잭션 여부가 결정된다.
`service` / `repository` 는 절대 `commit()` 하지 않는다 — 커밋은 여기서만 일어난다.

    @router.get('/{pk}')
    async def get_post(db: ConnDep, pk: int): ...        # 읽기: 끝나면 무조건 롤백

    @router.post('')
    async def create_post(db: TxDep, obj: CreatePostRequest): ...  # 쓰기: 자동 커밋/롤백

**읽기도 트랜잭션 안에서 돈다.** 한 요청이 두 번 조회하는 사이에 남의 커밋이 끼어들면
같은 요청 안에서 앞뒤가 다른 데이터를 본다. 끝에 롤백하는 것은 두 가지를 동시에 준다 —
요청 하나가 일관된 스냅샷을 보고, 읽기 의존성으로 들어온 쓰기는 밖으로 나가지 않는다.

연결 공급자는 `app.state` 에서 가져온다. 모듈 전역 엔진을 두지 않는 이유는 §2.1.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncTransaction

#: 연결 하나를 빌려주는 async 컨텍스트 매니저 팩토리. 운영에서는 `engine.connect`,
#: 테스트에서는 바깥 트랜잭션에 묶인 연결 하나를 계속 돌려주는 가짜다 (§2.8).
ConnectionSource = Callable[[], AbstractAsyncContextManager[AsyncConnection]]


def get_connection_source(request: Request) -> ConnectionSource:
    source = getattr(request.app.state, 'db_source', None)
    if source is None:
        # lifespan 이 돌지 않았다는 뜻이다. sys.exit() 하지 않고 예외만 올린다 (§3.3).
        raise RuntimeError('db_source 가 app.state 에 없다 — lifespan 이 실행되지 않았다')
    return source


async def begin(connection: AsyncConnection) -> AsyncTransaction:
    """트랜잭션을 연다. 이미 열려 있으면 SAVEPOINT 로.

    운영에서는 갓 빌린 연결이라 항상 바깥 트랜잭션이다. 테스트에서는 연결이 이미
    바깥 트랜잭션 안에 있어서 (§2.8) 여기서 SAVEPOINT 가 만들어진다 — 요청이 커밋해도
    테스트가 통째로 롤백할 수 있는 이유다. SQLite 에서 SAVEPOINT 가 제대로 돌려면
    드라이버의 자동 트랜잭션을 꺼야 한다 (`engine.py`).
    """
    return await (connection.begin_nested() if connection.in_transaction() else connection.begin())


@asynccontextmanager
async def write_transaction(source: ConnectionSource) -> AsyncGenerator[AsyncConnection, None]:
    """쓰기 트랜잭션 하나. 정상 종료 시 커밋, 예외 시 롤백.

    HTTP 를 모른다. 그래서 요청 밖에서도 쓸 수 있다 — 백그라운드 작업이 여기를
    거친다 (`bootstrap/jobs.py`). 커밋 규칙이 두 벌이 되면 한쪽만 고치는 날이 온다.
    """
    async with source() as connection:
        transaction = await begin(connection)
        try:
            yield connection
        except BaseException:
            await transaction.rollback()
            raise
        else:
            await transaction.commit()


async def get_db(request: Request) -> AsyncGenerator[AsyncConnection, None]:
    """읽기 전용 연결. 트랜잭션을 열되 끝나면 무조건 롤백한다."""
    async with get_connection_source(request)() as connection:
        transaction = await begin(connection)
        try:
            yield connection
        finally:
            await transaction.rollback()


async def get_db_tx(request: Request) -> AsyncGenerator[AsyncConnection, None]:
    """쓰기 연결. 커밋·롤백 판정은 `write_transaction` 이 한다."""
    async with write_transaction(get_connection_source(request)) as connection:
        yield connection


ConnDep = Annotated[AsyncConnection, Depends(get_db)]
TxDep = Annotated[AsyncConnection, Depends(get_db_tx)]
