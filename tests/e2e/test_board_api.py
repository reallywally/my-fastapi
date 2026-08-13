"""게시판·글 API 를 라우터 관통으로 검증한다 (§4.10).

여기서 확인하는 것은 업무 규칙이 아니라 **계약**이다 (§0):
상태코드, 응답 모양, 에러 코드, 권한 경계.

**쓰기는 전부 401 이다.** 인증이 Phase 5 이므로 `PrincipalDep` 이 주체를 못 만든다.
가짜 주체를 넣어두면 인가가 걸린 척하는 엔드포인트가 되고, 그게 Phase 5 까지
살아남으면 그대로 구멍이다. 라우트를 지금 노출하는 이유는 §0 — OpenAPI 계약이
확정되어야 화면 작업을 병행할 수 있다.
"""

import pytest

from app.modules.board.post.model import PostStatus
from tests.factories import create_board, create_post, create_posts, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')

BOARDS = '/api/v1/boards'
POSTS = '/api/v1/posts'


async def _board_and_author(db):
    board = await create_board(db)
    user = await create_user(db)
    return board, user.id


# ------------------------------------------------------------------ 게시판


async def test_board_list_is_public(client, db):
    """목록이 곧 메뉴다. 로그인 전에도 보여야 한다."""
    board = await create_board(db)

    response = await client.get(BOARDS)

    assert response.status_code == 200
    assert board.slug in [item['slug'] for item in response.json()]


async def test_board_detail_returns_the_public_shape(client, db):
    board = await create_board(db)

    response = await client.get(f'{BOARDS}/{board.slug}')

    assert response.status_code == 200
    assert set(response.json()) == {
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


async def test_missing_board_returns_404_with_a_domain_code(client):
    response = await client.get(f'{BOARDS}/nope')

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'board.not_found'
    assert response.json()['error']['message'] == '게시판을 찾을 수 없습니다.'


async def test_board_errors_follow_accept_language(client):
    response = await client.get(f'{BOARDS}/nope', headers={'Accept-Language': 'en'})

    assert response.json()['error']['message'] == 'We could not find that board.'


async def test_creating_a_board_requires_authentication(client):
    response = await client.post(BOARDS, json={'slug': 'notice', 'name': '공지'})

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


async def test_updating_and_deleting_a_board_require_authentication(client, db):
    board = await create_board(db)

    assert (await client.patch(f'{BOARDS}/{board.slug}', json={'name': 'x'})).status_code == 401
    assert (await client.delete(f'{BOARDS}/{board.slug}')).status_code == 401


# --------------------------------------------------------------- 글 목록


async def test_post_list_uses_the_cursor_contract(client, db):
    board, author_id = await _board_and_author(db)
    await create_posts(db, 3, board_id=board.id, author_id=author_id)

    response = await client.get(f'{BOARDS}/{board.slug}/posts', params={'size': 2})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'items', 'next_cursor', 'has_next'}  # §4.3 — total 은 없다
    assert len(body['items']) == 2
    assert body['has_next'] is True


async def test_post_list_items_carry_no_body(client, db):
    """목록 20개에 본문을 다 실으면 응답이 메가바이트가 되고, 화면은 쓰지도 않는다."""
    board, author_id = await _board_and_author(db)
    await create_post(db, board_id=board.id, author_id=author_id, content='아주 긴 본문')

    body = (await client.get(f'{BOARDS}/{board.slug}/posts')).json()

    assert 'content' not in body['items'][0]
    assert '아주 긴 본문' not in (await client.get(f'{BOARDS}/{board.slug}/posts')).text


async def test_post_list_walks_all_pages_via_next_cursor(client, db):
    board, author_id = await _board_and_author(db)
    created = await create_posts(db, 5, board_id=board.id, author_id=author_id)

    seen: list[int] = []
    cursor = None
    while True:
        params = {'size': 2} | ({'cursor': cursor} if cursor else {})
        body = (await client.get(f'{BOARDS}/{board.slug}/posts', params=params)).json()
        seen.extend(item['id'] for item in body['items'])
        if not body['has_next']:
            break
        cursor = body['next_cursor']

    assert {post.id for post in created} <= set(seen)
    assert len(seen) == len(set(seen))


async def test_post_list_rejects_an_oversized_page(client, db):
    """상한이 없으면 한 요청으로 게시판 전체를 긁어갈 수 있다."""
    board = await create_board(db)

    response = await client.get(f'{BOARDS}/{board.slug}/posts', params={'size': 1000})

    assert response.status_code == 422


async def test_listing_posts_of_a_missing_board_is_404(client):
    """권한보다 존재가 먼저다. 없는 게시판에 401 을 내면 로그인해도 달라지지 않는다."""
    response = await client.get(f'{BOARDS}/nope/posts')

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'board.not_found'


async def test_a_non_public_board_hides_its_posts(client, db):
    """§4.6 — `read_role` 이 anonymous 가 아니면 주체가 필요하다. 지금은 401."""
    board = await create_board(db, read_role='member')

    response = await client.get(f'{BOARDS}/{board.slug}/posts')

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


# --------------------------------------------------------------- 글 상세


async def test_post_detail_returns_the_public_shape(client, db):
    board, author_id = await _board_and_author(db)
    post = await create_post(db, board_id=board.id, author_id=author_id)

    response = await client.get(f'{POSTS}/{post.id}')

    assert response.status_code == 200
    body = response.json()
    assert body['id'] == post.id
    assert body['content'] == post.content
    assert set(body) == {
        'id',
        'board_id',
        'author_id',
        'title',
        'content',
        'is_pinned',
        'status',
        'view_count',
        'comment_count',
        'created_at',
        'updated_at',
    }


async def test_missing_post_returns_404_with_a_domain_code(client):
    response = await client.get(f'{POSTS}/999999')

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'post.not_found'
    assert response.json()['error']['message'] == '글을 찾을 수 없습니다.'


async def test_a_draft_is_not_reachable(client, db):
    board, author_id = await _board_and_author(db)
    draft = await create_post(db, board_id=board.id, author_id=author_id, status=PostStatus.draft)

    assert (await client.get(f'{POSTS}/{draft.id}')).status_code == 404


async def test_reading_a_post_does_not_bump_the_view_count(client, db):
    """§4.5 — 읽기가 쓰기가 되면 안 된다. 상세 조회는 `ConnDep` 이다."""
    board, author_id = await _board_and_author(db)
    post = await create_post(db, board_id=board.id, author_id=author_id)

    for _ in range(3):
        await client.get(f'{POSTS}/{post.id}')

    assert (await client.get(f'{POSTS}/{post.id}')).json()['view_count'] == 0


# --------------------------------------------------------------- 글 쓰기


async def test_writing_a_post_requires_authentication(client, db):
    board, _ = await _board_and_author(db)

    response = await client.post(f'{BOARDS}/{board.slug}/posts', json={'title': '제목', 'content': '본문'})

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


async def test_updating_and_deleting_a_post_require_authentication(client, db):
    board, author_id = await _board_and_author(db)
    post = await create_post(db, board_id=board.id, author_id=author_id)

    assert (await client.patch(f'{POSTS}/{post.id}', json={'title': 'x'})).status_code == 401
    assert (await client.delete(f'{POSTS}/{post.id}')).status_code == 401


async def test_authentication_is_checked_before_the_body(client, db):
    """본문이 엉망이어도 401 이 먼저다.

    인증 안 된 호출자에게 422 를 주면 어떤 필드가 있고 어떤 제약이 걸렸는지를 알려주는
    셈이다. 스키마 검증 자체는 유닛 테스트가 본다 (`tests/unit/test_board_schema.py`).
    """
    board = await create_board(db, write_role='anonymous')

    response = await client.post(f'{BOARDS}/{board.slug}/posts', json={'title': '', 'content': ''})

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


# --------------------------------------------------------------- 계약 (§0)


async def test_the_openapi_schema_exposes_the_board_contract(client):
    schema = (await client.get('/api/v1/openapi.json')).json()
    paths = schema['paths']

    assert '/api/v1/boards' in paths
    assert '/api/v1/boards/{slug}/posts' in paths
    assert '/api/v1/posts/{pk}' in paths
    # tag_함수명 형태 (§1.5)
    assert 'post_list_posts' in str(schema)
