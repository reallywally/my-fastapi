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
