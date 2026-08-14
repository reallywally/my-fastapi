"""첨부의 응답 계약 (§1.2, §0).

**요청 DTO 가 없다.** 업로드 본문은 JSON 이 아니라 multipart 라 Pydantic 모델로
받지 않는다 — 파일은 라우터가 `UploadFile` 로 받고, 검증도 거기서 한다 (§4.9).

**응답에 접근 URL 을 담는다** (§0). 화면이 `/attachments/` + id 를 조립하게 만들면
경로를 바꾸는 순간 화면이 깨지고, 저장소를 S3 로 바꿔도 화면을 고쳐야 한다.
URL 은 서버가 정하고 화면은 받은 것을 쓴다.
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.modules.board.attachment.model import Attachment


class AttachmentResponse(BaseModel):
    """첨부 하나. `storage_key` 는 나가지 않는다 — 저장소 내부 구조다 (규칙 #24)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int | None
    filename: str
    content_type: str
    size: int
    url: str
    created_at: datetime

    @classmethod
    def of(cls, row: Attachment) -> Self:
        """행을 응답으로. URL 조립이 여기 한 곳에만 있다.

        S3 서명 URL 로 바뀌어도 바뀌는 것은 이 메서드다. 다만 그때도 **권한 검사를
        거친 뒤에** 발급해야 한다 — 지금 URL 이 우리 라우트를 가리키는 이유이기도 하다
        (§4.6 의 읽기 권한이 다운로드에도 걸린다).
        """
        return cls(
            id=row.id,
            post_id=row.post_id,
            filename=row.filename,
            content_type=row.content_type,
            size=row.size,
            url=f'{get_settings().api_prefix}/attachments/{row.id}',
            created_at=row.created_at,
        )
