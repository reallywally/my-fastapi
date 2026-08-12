"""에러는 메시지가 아니라 **코드**로 raise 한다 (§2.6, 규칙 #7).

FBA 는 `raise NotFoundError(msg='用户不存在')` 였다. i18n 모듈이 있는데도 비즈니스
에러에는 적용되지 않았고, 결국 299개 파일 중 213개가 중국어로 굳었다. 메시지를
코드에 박으면 언어를 바꿀 방법이 없다.

    raise NotFoundError(code='post.not_found')          # 메시지 아님, 코드

메시지는 `locale/{ko,en}.json` 이 갖는다. 렌더링은 예외 핸들러가 한다.
`code` 는 생성자에서 **필수**다 — 잊으면 타입 에러가 난다.
"""

from typing import Any

from fastapi import status


class AppError(Exception):
    """모든 업무 예외의 조상.

    `status_code` 는 클래스가 정한다. 서비스는 어떤 HTTP 상태가 나갈지 몰라도 된다 —
    "없다/권한 없다/충돌이다" 만 말하면 된다 (§2.7 의 연장선).
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_code: str = 'internal.error'

    def __init__(self, *, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        self.code = code or self.default_code
        self.details = details or {}
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f'{type(self).__name__}(code={self.code!r})'


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'request.invalid'


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = 'auth.unauthorized'


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = 'auth.forbidden'


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = 'resource.not_found'


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'resource.conflict'


class UnprocessableError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    default_code = 'request.unprocessable'


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = 'request.rate_limited'


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = 'service.unavailable'


class UpstreamError(AppError):
    """외부 서버 호출 실패 (§5).

    **업스트림의 상태코드를 그대로 우리 응답으로 흘리지 않는다.** 상대가 404를 줬다고
    우리가 404를 주면, 클라이언트는 우리 리소스가 없는 건지 남의 리소스가 없는 건지
    구분할 수 없다. 의미 부여는 gateway 가 한다 — 여기까지 올라온 것은 '처리하지 못한
    외부 실패' 이고 502 다.

    `upstream` / `upstream_status` 는 파이썬 속성으로만 둔다. `details` 에 넣으면
    응답 본문으로 나가서 내부 구조가 드러난다 (규칙 #20).
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = 'upstream.error'

    def __init__(
        self,
        *,
        upstream: str,
        code: str | None = None,
        upstream_status: int | None = None,
    ) -> None:
        self.upstream = upstream
        self.upstream_status = upstream_status
        super().__init__(code=code)

    def __repr__(self) -> str:
        return f'{type(self).__name__}(upstream={self.upstream!r}, status={self.upstream_status!r})'


class UpstreamTimeoutError(UpstreamError):
    """연결·읽기·풀 대기 초과. 재시도해도 안 됐다는 뜻이다."""

    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    default_code = 'upstream.timeout'


class UpstreamUnavailableError(UpstreamError):
    """연결 자체가 안 된다 (DNS, TCP, TLS)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = 'upstream.unavailable'


class UpstreamStatusError(UpstreamError):
    """2xx 가 아닌 응답. **gateway 가 잡아서 도메인 에러로 바꾸는 것이 정상 경로다.**"""

    default_code = 'upstream.bad_status'


class UpstreamPayloadError(UpstreamError):
    """응답이 왔지만 우리가 아는 모양이 아니다 — 상대가 계약을 바꿨다는 신호."""

    default_code = 'upstream.bad_payload'


#: HTTPException 등 코드가 없는 예외를 상태값으로 코드에 매핑한다.
STATUS_TO_CODE: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: BadRequestError.default_code,
    status.HTTP_401_UNAUTHORIZED: UnauthorizedError.default_code,
    status.HTTP_403_FORBIDDEN: ForbiddenError.default_code,
    status.HTTP_404_NOT_FOUND: NotFoundError.default_code,
    status.HTTP_405_METHOD_NOT_ALLOWED: 'request.method_not_allowed',
    status.HTTP_409_CONFLICT: ConflictError.default_code,
    status.HTTP_422_UNPROCESSABLE_CONTENT: UnprocessableError.default_code,
    status.HTTP_429_TOO_MANY_REQUESTS: TooManyRequestsError.default_code,
    status.HTTP_503_SERVICE_UNAVAILABLE: ServiceUnavailableError.default_code,
}
