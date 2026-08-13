"""게시판·글 스키마 (§1.2, §0). DB 없이 밀리초 단위로 돈다.

응답 스키마가 **허용 목록**인지도 여기서 본다. 모델 필드를 늘려도 응답에 새어나가면
안 된다 — 화면이 의존하는 계약이 조용히 넓어지는 것도 계약 위반이다.
"""

import pytest
from pydantic import ValidationError

from app.modules.board.board.schema import BoardOut, CreateBoard, UpdateBoard
from app.modules.board.post.model import PostStatus
from app.modules.board.post.schema import CreatePost, PostOut, PostSummary, UpdatePost


def _board(**overrides) -> dict:
    return {'slug': 'notice', 'name': '공지사항'} | overrides


def _post(**overrides) -> dict:
    return {'title': '제목', 'content': '본문'} | overrides


# ------------------------------------------------------------------ 게시판


def test_board_defaults_are_public_read_member_write():
    board = CreateBoard(**_board())

    assert board.read_role == 'anonymous'
    assert board.write_role == 'member'
    assert board.allow_comment is True
    assert board.display_order == 0


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('slug', 'A'),  # 대문자 금지 — URL 식별자다
        ('slug', 'has space'),
        ('slug', 'x'),  # 2자 미만
        ('slug', 'x' * 51),
        ('name', ''),
        ('name', 'x' * 101),
    ],
)
def test_board_rejects_bad_values(field: str, value: str):
    with pytest.raises(ValidationError):
        CreateBoard(**_board(**{field: value}))


def test_board_slug_cannot_be_updated():
    """URL 식별자가 바뀌면 그 게시판을 가리키던 모든 링크가 깨진다."""
    assert 'slug' not in UpdateBoard.model_fields


def test_board_update_only_reports_the_fields_that_were_sent():
    assert UpdateBoard(name='새 이름').changes() == {'name': '새 이름'}
    assert UpdateBoard().changes() == {}
    assert UpdateBoard(name=None).changes() == {}


def test_board_out_is_an_allow_list():
    assert set(BoardOut.model_fields) == {
        'id',
        'slug',
        'name',
        'description',
        'read_role',
        'write_role',
        'allow_comment',
        'allow_attachment',
        'display_order',
    }


# ---------------------------------------------------------------------- 글


def test_post_defaults_to_published():
    assert CreatePost(**_post()).status is PostStatus.published


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('title', ''),
        ('title', 'x' * 201),
        ('content', ''),
        ('content', 'x' * 100_001),  # 상한이 없으면 그게 곧 무제한 업로드다
    ],
)
def test_post_rejects_bad_values(field: str, value: str):
    with pytest.raises(ValidationError):
        CreatePost(**_post(**{field: value}))


def test_post_board_cannot_be_changed():
    """글을 다른 게시판으로 옮기면 권한 판정이 달라진다 (§4.6)."""
    assert 'board_id' not in UpdatePost.model_fields


def test_post_update_only_reports_the_fields_that_were_sent():
    assert UpdatePost(title='제목만').changes() == {'title': '제목만'}
    assert UpdatePost().changes() == {}


def test_the_list_item_carries_no_body():
    """목록 20개에 본문을 다 실으면 응답이 메가바이트가 되고, 화면은 쓰지도 않는다."""
    assert 'content' not in PostSummary.model_fields
    assert 'content' in PostOut.model_fields


def test_post_detail_extends_the_list_item():
    """상세는 목록 항목의 상위집합이다 — 화면이 두 모양을 따로 다루지 않아도 된다."""
    assert set(PostSummary.model_fields) < set(PostOut.model_fields)
