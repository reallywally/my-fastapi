"""사용자 API 를 라우터 관통으로 검증한다 (Phase 3).

여기서 확인하는 것은 업무 규칙이 아니라 **계약**이다 (§0):
상태코드, 응답 모양, 에러 코드, 트랜잭션 경계.
"""

import pytest

from app.modules.user.repository import user_repository
from tests.factories import DEFAULT_PASSWORD, create_user, create_users

pytestmark = pytest.mark.asyncio(loop_scope='session')

USERS = '/api/v1/users'


def _payload(**overrides) -> dict:
    return {
        'username': 'gildong',
        'email': 'gildong@example.com',
        'nickname': '홍길동',
        'password': DEFAULT_PASSWORD,
    } | overrides


async def test_signup_returns_201_and_the_public_shape(client):
    response = await client.post(USERS, json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body['username'] == 'gildong'
    assert set(body) == {'id', 'username', 'email', 'nickname', 'status', 'created_at'}


async def test_signup_never_echoes_the_password(client):
    response = await client.post(USERS, json=_payload())

    assert DEFAULT_PASSWORD not in response.text
    assert 'password' not in response.json()
    assert 'argon2' not in response.text


async def test_signup_conflict_reports_which_field_collided(client, db_connection):
    await client.post(USERS, json=_payload())

    response = await client.post(USERS, json=_payload(email='other@example.com'))

    assert response.status_code == 409
    assert response.json()['error']['code'] == 'user.username_taken'


async def test_signup_validation_error_names_the_field(client):
    response = await client.post(USERS, json=_payload(username='ab'))

    assert response.status_code == 422
    body = response.json()
    assert body['error']['code'] == 'request.unprocessable'
    assert body['error']['details']['fields'][0]['field'] == 'username'


async def test_get_missing_user_returns_404_with_a_domain_code(client):
    response = await client.get(f'{USERS}/999999')

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'user.not_found'
    assert response.json()['error']['message'] == '사용자를 찾을 수 없습니다.'


async def test_error_messages_follow_accept_language(client):
    response = await client.get(f'{USERS}/999999', headers={'Accept-Language': 'en'})

    assert response.json()['error']['code'] == 'user.not_found'
    assert response.json()['error']['message'] == 'We could not find that user.'


async def test_get_returns_the_created_user(client):
    created = (await client.post(USERS, json=_payload())).json()

    response = await client.get(f'{USERS}/{created["id"]}')

    assert response.status_code == 200
    assert response.json()['id'] == created['id']


async def test_list_uses_the_cursor_contract(client, db):
    await create_users(db, 3)

    response = await client.get(USERS, params={'size': 2})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'items', 'next_cursor', 'has_next'}  # §4.3 — total 은 없다
    assert len(body['items']) == 2
    assert body['has_next'] is True


async def test_list_rejects_an_oversized_page(client):
    """상한이 없으면 한 요청으로 전체를 긁어갈 수 있다."""
    response = await client.get(USERS, params={'size': 1000})

    assert response.status_code == 422


async def test_list_walks_all_pages_via_next_cursor(client, db):
    created = await create_users(db, 5)

    seen: list[int] = []
    cursor = None
    while True:
        params = {'size': 2} | ({'cursor': cursor} if cursor else {})
        body = (await client.get(USERS, params=params)).json()
        seen.extend(item['id'] for item in body['items'])
        if not body['has_next']:
            break
        cursor = body['next_cursor']

    assert set(created_ids := {user.id for user in created}) <= set(seen)
    assert len(seen) == len(set(seen))
    assert created_ids


async def test_update_requires_authentication(client, db):
    """Phase 4 까지 `modules/user/deps.py` 가 401 을 낸다 — 가짜 주체를 넣지 않는다."""
    user = await create_user(db)

    response = await client.patch(f'{USERS}/{user.id}', json={'nickname': '변경'})

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


async def test_delete_requires_authentication(client, db):
    user = await create_user(db)

    response = await client.delete(f'{USERS}/{user.id}')

    assert response.status_code == 401


async def test_a_failed_write_leaves_nothing_behind(client, db_connection, db):
    """§1.1 — 409 가 난 요청의 쓰기는 롤백되어야 한다. TxDep 가 처리한다."""
    await client.post(USERS, json=_payload())
    before = (await client.get(USERS, params={'size': 100})).json()['items']

    conflicted = await client.post(USERS, json=_payload(email='other@example.com'))
    after = (await client.get(USERS, params={'size': 100})).json()['items']

    assert conflicted.status_code == 409
    assert len(after) == len(before)


async def test_deleted_users_disappear_from_the_api(client, db):
    user = await create_user(db)
    await user_repository.mark_deleted(db, user.id)

    assert (await client.get(f'{USERS}/{user.id}')).status_code == 404


async def test_the_openapi_schema_does_not_mention_the_hash(client):
    """§0 — 생성된 클라이언트에 password_hash 필드가 생기면 안 된다."""
    schema = (await client.get('/api/v1/openapi.json')).json()

    assert 'password_hash' not in str(schema)
    assert 'user_create_user' in str(schema)  # tag_함수명 형태 (§1.5)
