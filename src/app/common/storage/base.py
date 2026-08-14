"""파일 저장소 인터페이스 (§4.9).

**바이트만 안다.** `UploadFile` 도 `Request` 도 모르고, 게시글이나 첨부파일이라는
개념도 모른다 — `common` 이 도메인을 모른다는 §2.2 가 여기서도 성립한다. 라우터가
업로드를 청크 스트림으로 바꿔서 넘기고, 돌려받은 `StoredFile` 의 키만 서비스로 간다.

인터페이스를 두는 이유는 로컬/S3 교체다. 구현이 바뀌어도 바뀌지 않아야 하는 것:

- **저장 파일명은 저장소가 정한다.** 원본 이름을 경로에 쓰면 `../../etc/passwd` 가
  경로가 된다. 원본 이름은 DB 의 컬럼일 뿐이다
- **크기 상한은 쓰면서 잰다.** 다 받은 뒤에 재면 상한이 디스크를 지켜주지 못한다
- **읽기는 스트림이다.** 파일 경로를 돌려주면 S3 구현이 그 계약을 지킬 수 없다
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredFile:
    """저장 결과. 키는 저장소가 정하고, 크기는 저장하면서 잰 값이다."""

    key: str
    size: int


class FileTooLargeError(Exception):
    """상한을 넘겼다. 라우터가 413 으로 바꾼다 — 저장소는 HTTP 를 모른다."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f'파일이 상한({limit} bytes)을 넘겼다')


@runtime_checkable
class Storage(Protocol):
    """로컬/S3 구현이 만족해야 하는 계약."""

    async def save(self, chunks: AsyncIterator[bytes], *, suffix: str, max_bytes: int) -> StoredFile:
        """스트림을 저장하고 키를 돌려준다. `max_bytes` 를 넘으면 `FileTooLargeError`."""
        ...

    def read(self, key: str) -> AsyncIterator[bytes]:
        """저장된 바이트를 스트림으로. 없으면 `FileNotFoundError`."""
        ...

    async def delete(self, key: str) -> bool:
        """지운다. 원래 없었으면 False — 두 번 지워도 예외가 아니다."""
        ...

    async def keys(self) -> list[str]:
        """저장된 모든 키. 고아 정리 배치가 쓴다 (§4.9)."""
        ...

    async def modified_at(self, key: str) -> float | None:
        """마지막 수정 시각(epoch 초). 없으면 None.

        고아 판정에 필요하다 — 방금 올라온 파일은 아직 행이 없는 것이 정상이다.
        """
        ...
