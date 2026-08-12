"""업스트림 호출의 전송 계층 (§5). **업스트림이 무엇을 하는 서버인지는 모른다.**

여기가 책임지는 것: 타임아웃, 재시도, 커넥션 격리, 요청 ID 전파, 실패의 예외 변환.
여기가 책임지지 않는 것: 응답의 의미. 그건 `modules/*/gateway.py` 가 한다.

## 재시도는 멱등한 메서드에만

GET/HEAD/OPTIONS/PUT/DELETE 는 재시도한다. **POST/PATCH 는 하지 않는다.**
타임아웃은 "상대가 처리하지 않았다" 가 아니라 "결과를 못 봤다" 는 뜻이다. POST 를
재시도하면 같은 요청이 두 번 처리될 수 있다. 호출자가 멱등성을 보장할 수 있으면
`idempotent=True` 로 명시하게 한다 — 기본값이 안전한 쪽이어야 한다.

## 재시도 대상 상태코드

429, 502, 503, 504 와 전송 오류만. **500 은 재시도하지 않는다** — 보통 상대의 버그이고,
재시도는 장애 중인 서버에 부하를 보태는 것 말고는 하는 일이 없다.

## 이 클라이언트는 상태를 갖지 않는다

인스턴스 하나가 동시 요청 여러 개에 쓰인다. 재시도 상태 같은 것을 `self` 에 두면
요청끼리 서로 덮어쓴다 — 재현이 어려운 종류의 버그다. 전부 지역 변수로 둔다.
"""

import asyncio
import logging
import random
from typing import Any, Final

import httpx

from app.common.errors.exceptions import (
    UpstreamError,
    UpstreamPayloadError,
    UpstreamStatusError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from app.common.middleware import request_id_ctx
from app.core.constants import REQUEST_ID_HEADER
from app.core.upstream import UpstreamConfig

logger = logging.getLogger(__name__)

IDEMPOTENT_METHODS: Final = frozenset({'GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE'})
RETRYABLE_STATUSES: Final = frozenset({429, 502, 503, 504})
#: 상대가 준 Retry-After 를 그대로 믿지 않는다. 몇 분을 기다리라고 하면 요청이 매달린다.
MAX_RETRY_AFTER_SECONDS: Final = 5.0


class UpstreamClient:
    """업스트림 하나에 대응하는 클라이언트. lifespan 이 만들고 registry 가 들고 있다."""

    def __init__(self, name: str, config: UpstreamConfig, client: httpx.AsyncClient) -> None:
        self.name = name
        self.config = config
        self._client = client

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> httpx.Response:
        """2xx 응답을 돌려주거나 `UpstreamError` 를 올린다.

        `idempotent` 를 주지 않으면 HTTP 메서드로 판단한다.
        """
        verb = method.upper()
        retryable = verb in IDEMPOTENT_METHODS if idempotent is None else idempotent
        attempts = 1 + (self.config.max_retries if retryable else 0)

        transport_failure: Exception | None = None
        transient_status: int | None = None
        retry_after: str | None = None

        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(self._backoff(attempt, retry_after))
                retry_after = None

            try:
                response = await self._client.request(
                    verb, path, params=params, json=json, headers=self._headers(headers)
                )
            except httpx.TimeoutException as exc:
                transport_failure, transient_status = exc, None
                self._log_attempt(verb, path, attempt, attempts, repr(exc))
                continue
            except httpx.HTTPError as exc:
                # DNS/TCP/TLS/프로토콜 오류. 대개 같은 결과지만 일시적일 수 있다.
                transport_failure, transient_status = exc, None
                self._log_attempt(verb, path, attempt, attempts, repr(exc))
                continue

            if response.is_success:
                return response

            if response.status_code in RETRYABLE_STATUSES:
                transport_failure, transient_status = None, response.status_code
                retry_after = response.headers.get('retry-after')
                self._log_attempt(verb, path, attempt, attempts, f'status={response.status_code}')
                continue

            # 재시도할 값이 아니다 — 상대가 확정된 답을 줬다. gateway 가 의미를 정한다.
            logger.warning('upstream %s returned %s for %s %s', self.name, response.status_code, verb, path)
            raise UpstreamStatusError(upstream=self.name, upstream_status=response.status_code)

        raise self._exhausted(transport_failure, transient_status)

    async def get_json(self, path: str, **kwargs: Any) -> Any:
        return self.decode_json(await self.request('GET', path, **kwargs))

    def decode_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            logger.warning('upstream %s returned a non-JSON body', self.name)
            raise UpstreamPayloadError(upstream=self.name, upstream_status=response.status_code) from exc

    # ------------------------------------------------------------------ 내부

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        # 요청 ID 를 넘겨서 상대 로그와 우리 로그를 이어붙일 수 있게 한다 (§0).
        request_id = request_id_ctx.get()
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        if self.config.api_key is not None:
            headers[self.config.api_key_header] = self.config.api_key.get_secret_value()
        if extra:
            headers.update(extra)
        return headers

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                pass  # HTTP-date 형식이면 지수 백오프로 떨어진다.

        base = min(
            self.config.retry_backoff_seconds * (2 ** (attempt - 1)),
            self.config.retry_backoff_max_seconds,
        )
        # 지터를 섞는다. 없으면 워커들이 같은 시점에 동시에 재시도해서 상대를 다시 넘어뜨린다.
        return base * random.uniform(0.5, 1.5)  # noqa: S311 — 암호용이 아니라 thundering herd 방지용

    def _exhausted(self, failure: Exception | None, transient_status: int | None) -> UpstreamError:
        """재시도를 다 쓰고도 실패했을 때 무엇으로 올릴지.

        429/503 이 계속되는 것과 타임아웃은 다른 사건이다. 전자는 "상대가 과부하라
        나중에 다시 오라" 이므로 우리도 503 으로 전달하는 게 정확하다 — 클라이언트가
        재시도해도 되는 상황임을 알 수 있다.
        """
        if isinstance(failure, httpx.TimeoutException):
            return UpstreamTimeoutError(upstream=self.name)
        if failure is not None:
            return UpstreamUnavailableError(upstream=self.name)
        return UpstreamUnavailableError(upstream=self.name, upstream_status=transient_status)

    def _log_attempt(self, verb: str, path: str, attempt: int, attempts: int, reason: str) -> None:
        logger.warning('upstream %s %s %s failed (%s/%s): %s', self.name, verb, path, attempt + 1, attempts, reason)
