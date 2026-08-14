"""첨부 API 를 라우터 관통으로 검증한다 (§4.9, §4.10).

여기서 보는 것은 **계약**이다 (§0): 상태코드, 응답 모양, 에러 코드, 그리고
다운로드 응답의 헤더.

**업로드는 401 이다** — 인증이 Phase 5 이므로 `PrincipalDep` 이 주체를 못 만든다.
그래도 라우트를 지금 노출하는 이유는 §0 이다. 업로드 경로의 실제 동작(검증 → 저장 →
삽입)은 라우터 함수를 직접 호출해서 확인한다 — 주체가 생기면 그 자리에 e2e 가 붙는다.
"""

import pytest

from app.common.errors import BadRequestError
from app.common.security import Principal
from app.modules.board.attachment.router import upload_attachment
from tests.factories import create_attachment, create_board, create_post, create_user

pytestmark = pytest.mark.asyncio(loop_scope='session')

ATTACHMENTS = '/api/v1/attachments'
POSTS = '/api/v1/posts'


async def _post_with_author(db):
    board = await create_board(db)
    user = await create_user(db)
    return await create_post(db, board_id=board.id, author_id=user.id), user


class _Upload:
    """`UploadFile` 대신 쓰는 최소 스텁. 라우터가 쓰는 것은 `filename` 과 `read()` 뿐이다."""

    def __init__(self, filename: str | None, content: bytes) -> None:
        self.filename = filename
        self._content = content
        self._done = False

    async def read(self, _size: int = -1) -> bytes:
        if self._done:
            return b''
        self._done = True
        return self._content


# ------------------------------------------------------------------ 계약


async def test_uploading_requires_authentication(client, db):
    post, _ = await _post_with_author(db)

    response = await client.post(f'{POSTS}/{post.id}/attachments', files={'file': ('a.txt', b'hello')})

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'auth.unauthorized'


async def test_deleting_requires_authentication(client, db):
    post, author = await _post_with_author(db)
    attachment = await create_attachment(db, author_id=author.id, post_id=post.id)

    assert (await client.delete(f'{ATTACHMENTS}/{attachment.id}')).status_code == 401


async def test_listing_returns_the_public_shape(client, db):
    post, author = await _post_with_author(db)
    await create_attachment(db, author_id=author.id, post_id=post.id)

    response = await client.get(f'{POSTS}/{post.id}/attachments')

    assert response.status_code == 200
    item = response.json()[0]
    assert set(item) == {'id', 'post_id', 'filename', 'content_type', 'size', 'url', 'created_at'}
    # 저장소 내부 구조는 나가지 않는다 (규칙 #24).
    assert 'storage_key' not in item


async def test_the_response_carries_an_access_url(client, db):
    """§0 — 화면이 경로를 조립하게 만들지 않는다."""
    post, author = await _post_with_author(db)
    attachment = await create_attachment(db, author_id=author.id, post_id=post.id)

    item = (await client.get(f'{POSTS}/{post.id}/attachments')).json()[0]

    assert item['url'] == f'{ATTACHMENTS}/{attachment.id}'


async def test_a_missing_attachment_is_404_with_a_domain_code(client):
    response = await client.get(f'{ATTACHMENTS}/999999')

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'attachment.not_found'


async def test_a_non_public_board_hides_its_attachments(client, db):
    board = await create_board(db, read_role='member')
    user = await create_user(db)
    post = await create_post(db, board_id=board.id, author_id=user.id)
    attachment = await create_attachment(db, author_id=user.id, post_id=post.id)

    assert (await client.get(f'{ATTACHMENTS}/{attachment.id}')).status_code == 401
    assert (await client.get(f'{POSTS}/{post.id}/attachments')).status_code == 401


# ---------------------------------------------------- 업로드 (주체를 직접 넘긴다)


async def test_upload_stores_the_file_and_returns_its_url(db, storage):
    post, author = await _post_with_author(db)

    response = await upload_attachment(
        db=db,
        storage=storage,
        post_id=post.id,
        file=_Upload('보고서.txt', b'hello world'),
        actor=Principal(id=author.id),
    )

    assert response.size == 11
    assert response.content_type == 'text/plain'
    assert response.url.endswith(f'/attachments/{response.id}')


async def test_upload_never_uses_the_original_name_as_a_path(db, storage):
    """§4.9 — 저장 파일명은 서버가 정한다. 원본 이름은 DB 컬럼일 뿐이다."""
    post, author = await _post_with_author(db)

    await upload_attachment(
        db=db,
        storage=storage,
        post_id=post.id,
        file=_Upload('../../etc/passwd.txt', b'x'),
        actor=Principal(id=author.id),
    )

    assert not any('passwd' in key for key in await storage.keys())


async def test_an_unsupported_extension_is_refused(db, storage):
    post, author = await _post_with_author(db)

    with pytest.raises(BadRequestError) as exc:
        await upload_attachment(
            db=db,
            storage=storage,
            post_id=post.id,
            file=_Upload('payload.exe', b'x'),
            actor=Principal(id=author.id),
        )

    assert exc.value.code == 'attachment.unsupported_type'


async def test_an_empty_file_is_refused_and_leaves_nothing_behind(db, storage):
    post, author = await _post_with_author(db)
    before = set(await storage.keys())

    with pytest.raises(BadRequestError) as exc:
        await upload_attachment(
            db=db,
            storage=storage,
            post_id=post.id,
            file=_Upload('empty.txt', b''),
            actor=Principal(id=author.id),
        )

    assert exc.value.code == 'attachment.empty'
    assert set(await storage.keys()) == before


async def test_download_streams_the_bytes_as_an_attachment(client, db, storage):
    """다운로드는 **정적 파일 서빙이 아니라 API 다** — 읽기 권한이 걸린다 (§4.6).

    `Content-Disposition: attachment` 와 `nosniff` 는 XSS 차단이다. 우리 도메인에서
    열리는 사용자 파일은 그 자체로 통로가 된다.
    """
    post, author = await _post_with_author(db)
    uploaded = await upload_attachment(
        db=db,
        storage=storage,
        post_id=post.id,
        file=_Upload('메모.txt', b'hello world'),
        actor=Principal(id=author.id),
    )

    response = await client.get(f'{ATTACHMENTS}/{uploaded.id}')

    assert response.status_code == 200
    assert response.content == b'hello world'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['x-content-type-options'] == 'nosniff'


async def test_the_openapi_schema_exposes_the_attachment_contract(client):
    paths = (await client.get('/api/v1/openapi.json')).json()['paths']

    assert '/api/v1/posts/{post_id}/attachments' in paths
    assert '/api/v1/attachments/{pk}' in paths
