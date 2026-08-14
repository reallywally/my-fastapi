"""첨부파일 테이블 (§4.9).

**행과 파일은 다른 곳에 산다.** 행은 DB, 바이트는 저장소(`common/storage`)다. 둘을
잇는 것은 `storage_key` 하나고, 그래서 어긋날 수 있는 경우가 두 가지 생긴다:

- 파일은 있는데 행이 없다 → 업로드 뒤 트랜잭션이 실패한 자국. **고아 파일**이다
- 행은 있는데 파일이 없다 → 저장소 쪽 사고. 다운로드가 404 로 드러난다

첫 번째를 배치가 청소한다 (§4.9). 그래서 파일 삭제를 요청 트랜잭션 안에서 하지
않는다 — 파일은 롤백되지 않기 때문이다.

`post_id` 가 nullable 인 것도 같은 이유다. 글보다 파일이 먼저 올라오는 순서를
허용하면, 아직 어느 글에도 붙지 않은 파일이 정상 상태로 존재한다.

FK 컬럼에 `BigIntPK` 를 쓰는 것에 주의한다 (§4.2 와 같은 이유).
"""

from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Table

from app.common.db import BigIntPK, SoftDeletable, define_table

attachment_table: Table = define_table(
    'attachment',
    Column('post_id', BigIntPK, ForeignKey('post.id'), nullable=True),
    Column('author_id', BigIntPK, ForeignKey('user.id'), nullable=False),
    # 원본 파일명. **경로에 쓰지 않는다** — 저장 파일명은 저장소가 정한다 (§4.9).
    Column('filename', String(255), nullable=False),
    Column('content_type', String(100), nullable=False),
    Column('size', Integer, nullable=False),
    # 저장소 안의 키. 로컬이면 상대 경로, S3 면 오브젝트 키다.
    Column('storage_key', String(255), nullable=False),
    # 글 하나의 첨부 목록이 유일한 조회 경로다.
    Index('ix_attachment_post', 'post_id', 'deleted'),
)


@dataclass(slots=True)
class Attachment(SoftDeletable):
    TABLE: ClassVar[Table] = attachment_table

    post_id: int | None
    author_id: int
    filename: str
    content_type: str
    size: int
    storage_key: str
