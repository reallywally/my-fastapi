"""첨부의 업무 규칙 (§1.2). stateless + 모듈 전역 인스턴스 (§1.3).

**서비스는 `UploadFile` 을 받지 않는다** (§2.7, §4.9, 규칙 #5). 라우터가 저장까지
끝내고 원시 타입(파일명·크기·MIME·저장소 키)만 넘긴다. 그래서 이 파일은 파일이
어디에 어떻게 저장됐는지 모른다 — 로컬이든 S3 든 여기는 그대로다.

`attachment` 는 컨텍스트 안에서 `post` 위에 있다 (§4.1). `post` 를 import 하지만
반대는 없다 — 글은 자기 첨부를 모른다.
"""

from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.errors import ForbiddenError, NotFoundError
from app.common.security import Principal
from app.modules.board.attachment.model import Attachment
from app.modules.board.attachment.repository import attachment_repository
from app.modules.board.board.service import board_service
from app.modules.board.post.model import Post, PostStatus
from app.modules.board.post.repository import post_repository


class AttachmentService:
    @staticmethod
    async def _readable_post(*, db: AsyncConnection, post_id: int) -> Post:
        """첨부는 글에 딸린다. 글을 볼 수 없으면 첨부도 볼 수 없다.

        `comment` 와 같은 판정이고, 같은 이유로 `post_service` 를 부르지 않는다 (§4.1).
        """
        post = await post_repository.get(db, post_id)
        if post is None or post.status is not PostStatus.published:
            raise NotFoundError(code='post.not_found')
        await board_service.readable(db=db, board_id=post.board_id)
        return post

    @classmethod
    async def assert_can_attach(cls, *, db: AsyncConnection, post_id: int, actor: Principal) -> Post:
        """업로드해도 되는지 **저장하기 전에** 판정한다.

        순서가 중요하다. 저장부터 하고 나서 거절하면 디스크에 고아 파일이 남는다 —
        정리 배치가 있긴 하지만(§4.9), 막을 수 있는 쓰레기를 만들 이유는 없다.

        첨부를 올릴 수 있는 사람은 **글 작성자(또는 관리자)** 다 (§4.10 의 `[본인]`).
        """
        post = await post_repository.get(db, post_id)
        if post is None or post.status is not PostStatus.published:
            raise NotFoundError(code='post.not_found')

        # `_readable_post` 를 부르지 않는 이유는 게시판 행이 여기서 필요해서다 —
        # 두 번 부르면 같은 게시판을 두 번 읽는다.
        board = await board_service.readable(db=db, board_id=post.board_id)
        if not board.allow_attachment:
            raise ForbiddenError(code='attachment.not_allowed')
        if not actor.can_act_on(post.author_id):
            raise ForbiddenError(code='post.not_owner')
        return post

    @staticmethod
    async def attach(
        *,
        db: AsyncConnection,
        post_id: int,
        actor: Principal,
        filename: str,
        content_type: str,
        size: int,
        storage_key: str,
    ) -> Attachment:
        """저장이 끝난 파일을 글에 붙인다. 넘어오는 것은 전부 원시 타입이다."""
        return await attachment_repository.insert(
            db,
            post_id=post_id,
            author_id=actor.id,
            filename=filename,
            content_type=content_type,
            size=size,
            storage_key=storage_key,
        )

    @classmethod
    async def list(cls, *, db: AsyncConnection, post_id: int) -> list[Attachment]:
        await cls._readable_post(db=db, post_id=post_id)
        return await attachment_repository.list_for_post(db, post_id)

    @classmethod
    async def get_for_download(cls, *, db: AsyncConnection, pk: int, actor: Principal | None) -> Attachment:
        """다운로드 대상. **게시판 읽기 권한이 여기에도 걸린다** (§4.6).

        걸지 않으면 비공개 게시판의 파일이 첨부 id 만 알면 받아진다 — 글은 막고 첨부는
        여는 것은 막은 것이 아니다.

        아직 글에 붙지 않은 파일(§4.9 의 nullable `post_id`)은 판정할 게시판이 없다.
        그때는 올린 본인만 받을 수 있다.
        """
        attachment = await attachment_repository.get(db, pk)
        if attachment is None:
            raise NotFoundError(code='attachment.not_found')

        if attachment.post_id is None:
            if actor is None or not actor.can_act_on(attachment.author_id):
                raise NotFoundError(code='attachment.not_found')
            return attachment

        await cls._readable_post(db=db, post_id=attachment.post_id)
        return attachment

    @staticmethod
    async def delete(*, db: AsyncConnection, pk: int, actor: Principal) -> None:
        """삭제. 행만 지운다 — **파일은 정리 배치가 지운다** (§4.9).

        파일 삭제는 롤백되지 않는다. 같은 트랜잭션에서 지웠다가 뒤에서 예외가 나면
        행은 살아나고 파일만 사라진다. 그러면 다운로드가 500 을 내는 행이 남는다.
        """
        attachment = await attachment_repository.get(db, pk)
        if attachment is None:
            raise NotFoundError(code='attachment.not_found')
        if not actor.can_act_on(attachment.author_id):
            raise ForbiddenError(code='attachment.not_owner')
        await attachment_repository.mark_deleted(db, attachment.id)


attachment_service = AttachmentService()
