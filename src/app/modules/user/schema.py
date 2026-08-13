"""요청/응답 계약 (§1.2). 화면이 의존하는 모양이다 (§0).

`UserResponse` 은 **허용 목록**이다. 모델 필드를 늘려도 응답에 새어나가지 않는다 —
`password_hash` 가 실수로 노출되는 사고는 보통 "모델을 그대로 직렬화" 에서 난다.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.user.model import UserStatus

Username = Annotated[
    str,
    Field(min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_]+$', examples=['gildong']),
]
Nickname = Annotated[str, Field(min_length=1, max_length=50, examples=['홍길동'])]
#: 상한을 두는 이유는 성능이 아니라 DoS 다. argon2 는 입력 길이에 비례해 비싸다.
Password = Annotated[str, Field(min_length=8, max_length=128)]


class CreateUserRequest(BaseModel):
    username: Username
    email: EmailStr
    nickname: Nickname
    password: Password


class UpdateUserRequest(BaseModel):
    """부분 수정. 준 필드만 바뀐다 — `None` 과 '생략' 을 구분해야 해서 전부 Optional 이다."""

    email: EmailStr | None = None
    nickname: Nickname | None = None

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True, exclude_none=True)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    nickname: str
    status: UserStatus
    created_at: datetime
