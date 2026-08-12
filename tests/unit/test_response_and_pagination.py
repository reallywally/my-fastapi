"""응답 계약 (§0, §1.5, §4.3)."""

import msgspec
import pytest

from app.common.pagination import MAX_PAGE_SIZE, CursorParams, Page
from app.common.response import ErrorDetail, ErrorResponse, MsgspecJSONResponse


def test_msgspec_response_encodes():
    body = MsgspecJSONResponse(content={'a': 1, 'b': ['x']}).body

    assert msgspec.json.decode(body) == {'a': 1, 'b': ['x']}


def test_error_response_shape_is_fixed():
    """§0 — 화면은 error.code 로 분기한다. 이 모양이 바뀌면 화면이 깨진다."""
    payload = ErrorResponse(
        error=ErrorDetail(code='post.not_found', message='없습니다.'),
        request_id='abc',
    ).model_dump()

    assert payload == {
        'error': {'code': 'post.not_found', 'message': '없습니다.', 'details': {}},
        'request_id': 'abc',
    }


def test_page_has_no_total():
    """§4.3 — total 은 누락이 아니라 결정이다. COUNT(*) 를 매 요청 돌리지 않는다."""
    assert 'total' not in Page[int].model_fields


def test_page_slices_the_extra_row():
    """`limit(size + 1)` 로 한 개 더 읽어서 has_next 를 판정한다."""
    rows = [10, 9, 8, 7]  # size=3 인데 4개 읽어온 상황

    page = Page[int].of(rows, size=3, cursor_of=lambda row: row)

    assert page.items == [10, 9, 8]
    assert page.has_next is True
    assert page.next_cursor == 8


def test_page_on_the_last_page():
    page = Page[int].of([10, 9], size=3, cursor_of=lambda row: row)

    assert page.items == [10, 9]
    assert page.has_next is False
    assert page.next_cursor is None


def test_page_on_an_empty_result():
    page = Page[int].of([], size=3, cursor_of=lambda row: row)

    assert page.items == []
    assert page.has_next is False
    assert page.next_cursor is None


def test_page_size_is_capped():
    """상한이 없으면 한 요청으로 게시판 전체를 긁어갈 수 있다."""
    with pytest.raises(ValueError, match='less than or equal'):
        CursorParams(size=MAX_PAGE_SIZE + 1)
