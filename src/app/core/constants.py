"""설정·상수. 이 계층은 아무것도 import 하지 않는다 (§2.2)."""

from enum import StrEnum
from typing import Final


class Environment(StrEnum):
    local = 'local'
    test = 'test'
    staging = 'staging'
    production = 'production'


class JournalMode(StrEnum):
    """SQLite journal_mode. 운영은 wal, 테스트는 memory 가 빠르다."""

    wal = 'WAL'
    delete = 'DELETE'
    memory = 'MEMORY'


#: 요청 추적 헤더. 미들웨어가 붙이고 응답에도 되돌려준다 (§0).
REQUEST_ID_HEADER: Final = 'X-Request-ID'

#: 클라이언트가 보낸 요청 ID 를 그대로 신뢰하지 않기 위한 상한.
REQUEST_ID_MAX_LENGTH: Final = 64

#: 에러 메시지 카탈로그가 있는 언어 (§2.6). 없는 언어는 default_locale 로 떨어진다.
SUPPORTED_LOCALES: Final = ('ko', 'en')

#: soft delete 필터를 끄는 실행 옵션 키 (§2.4).
INCLUDE_DELETED: Final = 'include_deleted'
