"""에러 코드 → 사람이 읽는 메시지 (§2.6).

카탈로그는 `locale/{ko,en}.json` 이다. 로딩은 **지연**된다 — import 만으로 파일을
읽지 않는다(§2.1 과 같은 결). 프로세스당 한 번 읽고 캐시한다.

코드에 대응하는 메시지가 없으면 코드 자체를 돌려준다. 화면은 어차피 `code` 로
분기하므로(§0) 메시지 누락이 장애가 되지는 않는다 — 대신 로그로 남긴다.
"""

import json
import logging
from functools import lru_cache

from app.core.constants import SUPPORTED_LOCALES
from app.core.paths import LOCALE_DIR

logger = logging.getLogger(__name__)


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def _catalog(locale: str) -> dict[str, str]:
    path = LOCALE_DIR / f'{locale}.json'
    if not path.is_file():
        logger.warning('메시지 카탈로그가 없다: %s', path)
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def negotiate_locale(accept_language: str | None, default: str) -> str:
    """`Accept-Language` 에서 지원하는 첫 언어를 고른다.

    q-value 는 보지 않는다. 지원 언어가 두 개뿐이라 정렬해봐야 결과가 같다 —
    언어가 늘면 그때 제대로 구현한다.
    """
    if not accept_language:
        return default
    for part in accept_language.split(','):
        tag = part.split(';', 1)[0].strip().lower()
        primary = tag.split('-', 1)[0]
        if primary in SUPPORTED_LOCALES:
            return primary
    return default


def render(code: str, locale: str, default_locale: str) -> str:
    """코드를 메시지로 바꾼다. 요청 언어 → 기본 언어 → 코드 자체 순으로 떨어진다."""
    for candidate in (locale, default_locale):
        message = _catalog(candidate).get(code)
        if message is not None:
            return message
    logger.warning('메시지 카탈로그에 없는 에러 코드: %s', code)
    return code
