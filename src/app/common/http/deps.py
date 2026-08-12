"""업스트림 registry 의존성.

`db` / `redis` 와 같은 방식이다 (§2.1) — lifespan 이 `app.state` 에 올린 것을 꺼낸다.
"""

from typing import Annotated

from fastapi import Depends, Request

from app.common.http.registry import UpstreamRegistry


def get_upstreams(request: Request) -> UpstreamRegistry:
    registry = getattr(request.app.state, 'upstreams', None)
    if registry is None:
        raise RuntimeError('upstreams 가 app.state 에 없다 — lifespan 이 실행되지 않았다')
    return registry


UpstreamsDep = Annotated[UpstreamRegistry, Depends(get_upstreams)]
