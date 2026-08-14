"""쿼리만. 업무 규칙은 없다 (§1.2).

`commit()` 하지 않고 (§1.1), `deleted == 0` 을 손으로 쓰지 않는다 (§2.4).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.db import alive, all_of, one_or_none, select_alive, soft_delete
from app.modules.board.attachment.model import Attachment, attachment_table


class AttachmentRepository:
    @staticmethod
    async def get(db: AsyncConnection, pk: int) -> Attachment | None:
        result = await db.execute(select_alive(Attachment).where(attachment_table.c.id == pk))
        return one_or_none(Attachment, result)

    @staticmethod
    async def list_for_post(db: AsyncConnection, post_id: int) -> list[Attachment]:
        """한 글의 첨부 목록. 올린 순서 그대로다.

        커서를 쓰지 않는 유일한 목록이다 (규칙 #11 은 페이지가 깊어지는 목록에 대한
        규칙이다). 한 글의 첨부는 업로드 제한이 이미 상한이고, 화면은 항상 전부 쓴다.
        """
        statement = (
            select_alive(Attachment).where(attachment_table.c.post_id == post_id).order_by(attachment_table.c.id)
        )
        return all_of(Attachment, await db.execute(statement))

    @staticmethod
    async def insert(
        db: AsyncConnection,
        *,
        post_id: int | None,
        author_id: int,
        filename: str,
        content_type: str,
        size: int,
        storage_key: str,
    ) -> Attachment:
        result = await db.execute(
            sa.insert(attachment_table).values(
                post_id=post_id,
                author_id=author_id,
                filename=filename,
                content_type=content_type,
                size=size,
                storage_key=storage_key,
            )
        )
        row = await AttachmentRepository.get(db, result.inserted_primary_key[0])
        if row is None:  # pragma: no cover - 방금 넣은 행이 같은 트랜잭션에서 사라질 수는 없다
            raise RuntimeError('방금 삽입한 첨부를 다시 읽지 못했다')
        return row

    @staticmethod
    async def mark_deleted(db: AsyncConnection, pk: int) -> int:
        """§1.4 — `deleted = id`. **파일은 여기서 지우지 않는다.**

        파일 삭제는 롤백되지 않는다. 같은 트랜잭션 안에서 지우면 뒤에서 예외가 났을 때
        행은 살아나고 파일만 사라진다 — 정리 배치가 대신한다 (§4.9).
        """
        result = await db.execute(soft_delete(Attachment, attachment_table.c.id == pk))
        return result.rowcount

    @staticmethod
    async def list_unattached(db: AsyncConnection, *, before: datetime, limit: int) -> list[Attachment]:
        """아직 글에 붙지 않은 채 오래된 행. 정리 배치가 쓴다 (§4.9).

        `before` 를 두는 이유: 방금 올라온 미연결 파일은 고아가 아니라 **진행 중**이다.
        """
        statement = (
            select_alive(Attachment)
            .where(attachment_table.c.post_id.is_(None), attachment_table.c.created_at < before)
            .order_by(attachment_table.c.id)
            .limit(limit)
        )
        return all_of(Attachment, await db.execute(statement))

    @staticmethod
    async def protected_keys(db: AsyncConnection) -> set[str]:
        """정리 배치가 **건드리면 안 되는** 저장소 키 (§4.9).

        두 부류다:

        - 살아 있는 행의 파일 — 당연히 쓰이고 있다
        - 지워졌지만 **글에 붙어 있던** 행의 파일 — soft delete 는 복구를 전제로 한다
          (§1.4). 파일까지 없애면 복구해도 빈 껍데기가 돌아온다

        빠지는 것은 하나뿐이다: **지워졌고 글에도 붙지 않은 행.** 그 상태는 정리
        배치가 직접 만든 것이고 (TTL 이 지난 미연결 행), 되살릴 이유가 없다.
        """
        statement = sa.select(attachment_table.c.storage_key).where(
            sa.or_(alive(Attachment), attachment_table.c.post_id.isnot(None))
        )
        result = await db.execute(statement)
        return set(result.scalars())


attachment_repository = AttachmentRepository()
