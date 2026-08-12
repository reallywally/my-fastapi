"""에러 코드 체계와 i18n 카탈로그 (§2.6)."""

import ast
import json

import pytest

from app.common.errors import AppError, ForbiddenError, NotFoundError, negotiate_locale, render
from app.common.errors.exceptions import STATUS_TO_CODE
from app.core.constants import SUPPORTED_LOCALES
from app.core.paths import APP_DIR, LOCALE_DIR


def test_error_carries_a_code_not_a_message():
    """§2.6 — FBA 는 `msg='用户不存在'` 였다. 메시지를 코드에 박으면 번역할 방법이 없다."""
    error = NotFoundError(code='post.not_found')

    assert error.code == 'post.not_found'
    assert error.status_code == 404


def test_error_falls_back_to_a_class_default():
    assert ForbiddenError().code == 'auth.forbidden'
    assert AppError().code == 'internal.error'


def test_details_are_isolated_between_instances():
    """가변 기본값을 공유하면 한 요청의 details 가 다음 요청에 새어나간다."""
    first = NotFoundError()
    first.details['leak'] = True

    assert NotFoundError().details == {}


@pytest.mark.parametrize('locale', SUPPORTED_LOCALES)
def test_catalog_is_valid_json(locale: str):
    catalog = json.loads((LOCALE_DIR / f'{locale}.json').read_text(encoding='utf-8'))

    assert catalog
    assert all(isinstance(value, str) for value in catalog.values())


def test_catalogs_cover_the_same_codes():
    """한쪽에만 있는 코드는 언어를 바꾸는 순간 코드 문자열이 그대로 노출된다."""
    catalogs = {
        locale: set(json.loads((LOCALE_DIR / f'{locale}.json').read_text(encoding='utf-8')))
        for locale in SUPPORTED_LOCALES
    }
    reference = catalogs['ko']

    for locale, codes in catalogs.items():
        assert codes == reference, f'{locale} 카탈로그가 ko 와 다르다: {codes ^ reference}'


def test_every_builtin_error_code_has_a_message():
    """예외 클래스를 추가하고 카탈로그를 잊는 것을 여기서 잡는다."""
    defaults = {cls.default_code for cls in _all_subclasses(AppError)} | {AppError.default_code}
    defaults |= set(STATUS_TO_CODE.values())
    catalog = json.loads((LOCALE_DIR / 'ko.json').read_text(encoding='utf-8'))

    assert not defaults - set(catalog), f'카탈로그에 없는 코드: {sorted(defaults - set(catalog))}'


def _all_subclasses(cls: type) -> set[type]:
    direct = set(cls.__subclasses__())
    return direct.union(*(_all_subclasses(sub) for sub in direct)) if direct else direct


def test_every_code_raised_in_modules_has_a_message():
    """§2.6 — 도메인 코드도 카탈로그에 있어야 한다.

    빠지면 사용자에게 `user.not_found` 라는 날문자가 보인다. 500 이 아니라서 조용하다.
    AST 로 `code='...'` 키워드 인자만 본다 — 주석이나 독스트링은 세지 않는다.
    """
    catalog = set(json.loads((LOCALE_DIR / 'ko.json').read_text(encoding='utf-8')))

    raised: set[str] = set()
    for path in (APP_DIR / 'modules').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == 'code' and isinstance(keyword.value, ast.Constant):
                    raised.add(str(keyword.value.value))

    assert raised, 'modules/ 에서 raise 하는 에러 코드를 찾지 못했다'
    assert not raised - catalog, f'카탈로그에 없는 도메인 코드: {sorted(raised - catalog)}'


@pytest.mark.parametrize(
    ('header', 'expected'),
    [
        ('en', 'en'),
        ('en-US,en;q=0.9', 'en'),
        ('ko-KR,ko;q=0.9,en;q=0.8', 'ko'),
        ('fr,en;q=0.5', 'en'),
        ('fr-FR', 'ko'),
        (None, 'ko'),
        ('', 'ko'),
    ],
)
def test_locale_negotiation(header: str | None, expected: str):
    assert negotiate_locale(header, default='ko') == expected


def test_render_falls_back_to_default_locale_then_to_the_code():
    assert render('auth.forbidden', 'en', 'ko') == 'You do not have permission to do that.'
    # 카탈로그에 없는 코드는 코드 자체가 나온다 — 화면은 code 로 분기하므로 장애가 아니다.
    assert render('nope.unknown', 'en', 'ko') == 'nope.unknown'
