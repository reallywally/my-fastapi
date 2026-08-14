"""HTTP 만 다룬다 (§1.2). **파일을 아는 유일한 계층이다** (§4.9).

여기가 `UploadFile` 을 받아 저장까지 끝내고, 서비스에는 원시 타입만 넘긴다 (§2.7).
검증도 여기서 한다 — 확장자·크기는 HTTP 요청의 성질이지 업무 규칙이 아니다.

**저장 파일명은 서버가 정한다.** 원본 이름은 DB 컬럼으로만 남는다 (§4.9). 그리고
**MIME 도 서버가 정한다** — 클라이언트가 보낸 `Content-Type` 을 그대로 믿고 되돌려
주면, `text/html` 로 올린 파일이 우리 도메인에서 실행되는 스크립트가 된다.

경로가 두 갈래인 것은 글·댓글과 같다 (§4.10). 업로드·목록은 글에 딸리고,
다운로드·삭제는 첨부 id 하나로 충분하다.
"""

import mimetypes
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Final
from urllib.parse import quote

from fastapi import APIRouter, UploadFile, status
from fastapi.responses import StreamingResponse

from app.common.db import ConnDep, TxDep
from app.common.errors import BadRequestError
from app.common.storage import FileTooLargeError, StorageDep
from app.core.config import get_settings
from app.modules.board.attachment.schema import AttachmentResponse
from app.modules.board.attachment.service import attachment_service
from app.modules.user.deps import PrincipalDep

#: 한 번에 읽어 넘기는 크기. 업로드 전체를 메모리에 올리지 않는다.
UPLOAD_CHUNK_SIZE: Final = 64 * 1024

#: 확장자를 알아보지 못했을 때. 브라우저가 내용을 추측해서 실행하지 못하게 한다.
FALLBACK_CONTENT_TYPE: Final = 'application/octet-stream'

#: `/posts/{post_id}/attachments` — 글에 딸린 경로
post_router = APIRouter(prefix='/posts/{post_id}/attachments', tags=['attachment'])

#: `/attachments/{pk}` — 첨부 id 하나로 충분한 경로
router = APIRouter(prefix='/attachments', tags=['attachment'])


def _suffix(filename: str) -> str:
    """허용된 확장자를 소문자로 돌려준다. 아니면 400.

    확장자를 먼저 보는 이유는 저장소에 남길 이름을 여기서 정하기 때문이다.
    클라이언트가 준 `Content-Type` 은 판정에 쓰지 않는다 — 바꾸기가 너무 쉽다.
    """
    suffix = PurePosixPath(filename).suffix.lower().lstrip('.')
    if suffix not in get_settings().attachment_allowed_extensions:
        raise BadRequestError(code='attachment.unsupported_type')
    return f'.{suffix}'


def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    async def _iterate() -> AsyncIterator[bytes]:
        while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
            yield chunk

    return _iterate()


@post_router.post('', status_code=status.HTTP_201_CREATED, summary='첨부 업로드 (본인)')
async def upload_attachment(
    db: TxDep,
    storage: StorageDep,
    post_id: int,
    file: UploadFile,
    actor: PrincipalDep,
) -> AttachmentResponse:
    """**권한 판정 → 저장 → 행 삽입** 순서다.

    저장부터 하고 나서 거절하면 디스크에 고아 파일이 남는다. 반대로 행부터 넣으면
    파일이 없는 행이 생긴다 — 파일은 롤백되지 않으니 이 순서가 덜 나쁘다. 그래도
    남는 틈(저장 성공 + 삽입 실패)은 정리 배치가 맡는다 (§4.9).
    """
    settings = get_settings()
    await attachment_service.assert_can_attach(db=db, post_id=post_id, actor=actor)

    filename = PurePosixPath(file.filename or '').name
    if not filename:
        raise BadRequestError(code='attachment.no_filename')
    suffix = _suffix(filename)

    try:
        stored = await storage.save(_chunks(file), suffix=suffix, max_bytes=settings.attachment_max_bytes)
    except FileTooLargeError as exc:
        raise BadRequestError(code='attachment.too_large') from exc
    if stored.size == 0:
        await storage.delete(stored.key)
        raise BadRequestError(code='attachment.empty')

    attachment = await attachment_service.attach(
        db=db,
        post_id=post_id,
        actor=actor,
        filename=filename[:255],
        content_type=mimetypes.guess_type(filename)[0] or FALLBACK_CONTENT_TYPE,
        size=stored.size,
        storage_key=stored.key,
    )
    return AttachmentResponse.of(attachment)


@post_router.get('', summary='첨부 목록')
async def list_attachments(db: ConnDep, post_id: int) -> list[AttachmentResponse]:
    rows = await attachment_service.list(db=db, post_id=post_id)
    return [AttachmentResponse.of(row) for row in rows]


@router.get('/{pk}', summary='첨부 다운로드')
async def download_attachment(db: ConnDep, storage: StorageDep, pk: int) -> StreamingResponse:
    """§0 — 응답의 `url` 이 가리키는 자리다.

    **정적 파일 서빙이 아니라 API 다.** 게시판 읽기 권한이 여기에도 걸려야 하기
    때문이다 (§4.6) — 저장소 URL 을 그대로 내주면 그 검사를 우회할 수 있다.

    주체는 아직 없다 (Phase 5). 미연결 첨부는 본인만 받을 수 있으므로 지금은
    404 가 된다 — 열어두는 쪽으로 틀리지 않는다.
    """
    attachment = await attachment_service.get_for_download(db=db, pk=pk, actor=None)
    return StreamingResponse(
        storage.read(attachment.storage_key),
        media_type=attachment.content_type,
        headers={
            # 항상 내려받게 한다. 이미지·PDF 도 마찬가지다 — 우리 도메인에서 열리는
            # 사용자 파일은 그 자체로 XSS 통로다.
            #
            # 헤더는 latin-1 이라 한글 파일명을 그대로 넣으면 응답을 만들다 죽는다.
            # RFC 5987 의 `filename*` 로 퍼센트 인코딩해서 넣는다.
            'Content-Disposition': f"attachment; filename*=UTF-8''{quote(attachment.filename)}",
            'X-Content-Type-Options': 'nosniff',
        },
    )


@router.delete('/{pk}', status_code=status.HTTP_204_NO_CONTENT, summary='첨부 삭제 (본인 또는 관리자)')
async def delete_attachment(db: TxDep, pk: int, actor: PrincipalDep) -> None:
    await attachment_service.delete(db=db, pk=pk, actor=actor)
