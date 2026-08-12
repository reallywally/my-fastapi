"""이름 → 업스트림 클라이언트 (§5).

**서버가 n개로 늘어나도 코드가 늘지 않는다.** 설정에 항목을 추가하면 registry 가
클라이언트를 만든다. 서버마다 클래스를 만드는 방식은 3개째부터 복사-붙여넣기가 된다.

생성은 `create_registry()` 만 한다 — `httpx.AsyncClient` 는 I/O 자원이고 §2.1 에 따라
lifespan 에서만 열린다 (규칙 #3, #25).
"""

import logging

import httpx

from app.common.http.client import UpstreamClient
from app.core.upstream import UpstreamConfig

logger = logging.getLogger(__name__)


class UpstreamRegistry:
    def __init__(self, clients: dict[str, UpstreamClient]) -> None:
        self._clients = clients

    def get(self, name: str) -> UpstreamClient:
        """설정에 없는 이름은 **즉시 예외**다.

        `None` 을 돌려주면 호출자가 확인을 잊고, 설정 누락이 런타임의 엉뚱한 곳에서
        `AttributeError` 로 나타난다. 설정 오류는 설정 오류로 보여야 한다.
        """
        try:
            return self._clients[name]
        except KeyError as exc:
            known = ', '.join(sorted(self._clients)) or '(없음)'
            raise RuntimeError(f'업스트림 {name!r} 이 설정되어 있지 않다. 설정된 이름: {known}') from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    def __contains__(self, name: str) -> bool:
        return name in self._clients


def _build_client(config: UpstreamConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=str(config.base_url),
        # 타임아웃 4종을 전부 명시한다. httpx 기본값에 맡기면 pool 대기가 무제한이다.
        timeout=httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.pool_timeout_seconds,
        ),
        # 업스트림별 커넥션 상한 = bulkhead. 하나가 느려져도 다른 호출이 커넥션을 못 얻는 일이 없다.
        limits=httpx.Limits(
            max_connections=config.max_connections,
            max_keepalive_connections=config.max_keepalive_connections,
        ),
        verify=config.verify_tls,
        follow_redirects=False,
    )


def create_registry(upstreams: dict[str, UpstreamConfig]) -> tuple[UpstreamRegistry, list[httpx.AsyncClient]]:
    """(registry, 정리해야 할 httpx 클라이언트들) 을 돌려준다.

    닫아야 할 것을 따로 돌려주는 이유: registry 에 `aclose` 를 두면 도메인 코드가
    자원 수명을 만질 수 있게 된다. 수명은 lifespan 만 다룬다 (§2.1).
    """
    clients: dict[str, UpstreamClient] = {}
    raw: list[httpx.AsyncClient] = []

    for name, config in upstreams.items():
        http_client = _build_client(config)
        raw.append(http_client)
        clients[name] = UpstreamClient(name, config, http_client)

    logger.info('upstreams configured: %s', ', '.join(sorted(clients)) or '(없음)')
    return UpstreamRegistry(clients), raw
