"""저장소 의존성.

`db` · `redis` 와 같은 방식이다 (§2.1) — 인스턴스는 lifespan 이 만들어 `app.state` 에
두고, 라우터가 `Depends` 로 빌린다. 모듈 전역 인스턴스는 없다.
"""

from typing import Annotated

from fastapi import Depends, Request

from app.common.storage.base import Storage


def get_storage(request: Request) -> Storage:
    storage = getattr(request.app.state, 'storage', None)
    if storage is None:
        raise RuntimeError('storage 가 app.state 에 없다 — lifespan 이 실행되지 않았다')
    return storage


StorageDep = Annotated[Storage, Depends(get_storage)]
