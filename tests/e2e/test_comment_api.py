"""댓글 API 를 라우터 관통으로 검증한다 (§4.10).

여기서 확인하는 것은 업무 규칙이 아니라 **계약**이다 (§0):
상태코드, 응답 모양, 커서, 권한 경계.

쓰기는 전부 401 이다 — 인증이 Phase 5 다. 읽기는 `read_role='anonymous'` 게시판에
한해 열려 있다 (§4.6).
"""

import pytest

from app.common.security import Principal
from app.modules.board.comment.service import comment_service
from tests.factories import create_board, create_comment, create_post, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')

POSTS = '/api/v1/posts'
COMMENTS = '/api/v1/comments'


async def _post_with_author(db, **board_overrides):
    board = await create_board(db, **board_overrides)
    user = await create_user(db)
    post = await create_post(db, board_id=board.id, author_id=user.id)
    return post, user.id


# --------------------------------------------------------------- 트리 조회


async def test_thread_returns_the_page_contract(client, db):
    post, author_id = await _post_with_author(db)
    await create_comment(db, post_id=post.id, author_id=author_id)

    response = await client.get(f'{POSTS}/{post.id}/comments')

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'items', 'next_cursor', 'has_next'}  # §4.3 과 같은 모양
    assert set(body['items'][0]) == {
        'id',
        'post_id',
        'parent_id',
        'author_id',
        'content',
        'depth',
        'is_removed',
        'created_at',
    }


async def test_thread_is_ordered_by_path(client, db):
    post, author_id = await _post_with_author(db)
    first = await create_comment(db, post_id=post.id, author_id=author_id, content='첫째')
    await create_comment(db, post_id=post.id, author_id=author_id, parent=first, content='첫째의 답글')
    await create_comment(db, post_id=post.id, author_id=author_id, content='둘째')

    items = (await client.get(f'{POSTS}/{post.id}/comments')).json()['items']

    assert [item['content'] for item in items] == ['첫째', '첫째의 답글', '둘째']


async def test_thread_walks_all_pages_via_next_cursor(client, db):
    """커서가 `path` 문자열이다 — 정렬 키가 곧 커서라야 트리가 끊기지 않는다."""
    post, author_id = await _post_with_author(db)
    parents = [await create_comment(db, post_id=post.id, author_id=author_id) for _ in range(3)]
    for parent in parents:
        await create_comment(db, post_id=post.id, author_id=author_id, parent=parent)

    seen: list[int] = []
    cursor = None
    while True:
        params = {'size': 2} | ({'cursor': cursor} if cursor else {})
        body = (await client.get(f'{POSTS}/{post.id}/comments', params=params)).json()
        seen.extend(item['id'] for item in body['items'])
        if not body['has_next']:
            break
        cursor = body['next_cursor']

    assert len(seen) == 6
    assert len(seen) == len(set(seen))


async def test_thread_rejects_an_oversized_page(client, db):
    post, _ = await _post_with_author(db)

    assert (await client.get(f'{POSTS}/{post.id}/comments', params={'size': 1000})).status_code == 422


async def test_thread_of_a_missing_post_is_404(client):
    response = await client.get(f'{POSTS}/999999/comments')

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'post.not_found'


async def test_thread_of_a_private_board_is_401(client, db):
    """글을 볼 수 없으면 댓글도 볼 수 없다 (§4.6)."""
    post, _ = await _post_with_author(db, read_role='member')

    response = await client.get(f'{POSTS}/{post.id}/comments')

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


async def test_a_tombstone_is_masked_over_the_wire(client, db):
    """§4.7 — 트리에는 남고 내용·작성자는 가려진다."""
    post, author_id = await _post_with_author(db)
    parent = await create_comment(db, post_id=post.id, author_id=author_id, content='비밀 내용')
    await create_comment(db, post_id=post.id, author_id=author_id, parent=parent)

    await comment_service.delete(db=db, pk=parent.id, actor=Principal(id=author_id))

    response = await client.get(f'{POSTS}/{post.id}/comments')

    assert '비밀 내용' not in response.text
    tombstone = response.json()['items'][0]
    assert tombstone['is_removed'] is True
    assert tombstone['author_id'] is None


# --------------------------------------------------------------- 쓰기 (401)


async def test_writing_a_comment_requires_authentication(client, db):
    post, _ = await _post_with_author(db)

    response = await client.post(f'{POSTS}/{post.id}/comments', json={'content': '댓글'})

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


async def test_updating_and_deleting_a_comment_require_authentication(client, db):
    post, author_id = await _post_with_author(db)
    comment = await create_comment(db, post_id=post.id, author_id=author_id)

    assert (await client.patch(f'{COMMENTS}/{comment.id}', json={'content': 'x'})).status_code == 401
    assert (await client.delete(f'{COMMENTS}/{comment.id}')).status_code == 401


# --------------------------------------------------------------- 계약 (§0)


async def test_the_openapi_schema_exposes_the_comment_contract(client):
    paths = (await client.get('/api/v1/openapi.json')).json()['paths']

    assert '/api/v1/posts/{post_id}/comments' in paths
    assert '/api/v1/comments/{pk}' in paths
