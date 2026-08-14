"""로컬 디스크 구현 (§4.9).

개발과 단일 노드 배포용이다. 여러 노드로 늘어나면 S3 구현으로 갈아끼운다 — 그때
바뀌는 파일은 여기와 `lifespan` 한 줄이고, 도메인 코드는 그대로다.

**파일 I/O 는 블로킹이다.** async 함수 안에서 그냥 `write()` 를 부르면 그 순간
이벤트 루프가 멈추고, 큰 파일 하나가 서버 전체의 응답을 세운다. 그래서 실제 쓰기·읽기는
`asyncio.to_thread` 로 내보낸다.
"""

import asyncio
import re
import shutil
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from app.common.storage.base import FileTooLargeError, StoredFile

#: 한 번에 읽고 쓰는 크기. 업로드 전체를 메모리에 올리지 않기 위한 값이다.
CHUNK_SIZE: Final = 64 * 1024

#: 저장 키에 허용하는 모양. `2026/08/<32자 hex><확장자>` 만 나온다.
KEY_PATTERN: Final = re.compile(r'^\d{4}/\d{2}/[0-9a-f]{32}(\.[a-z0-9]{1,16})?$')


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """키를 경로로. **모양을 먼저 검사한다.**

        키는 DB 에서 오지만 그렇다고 믿을 이유는 없다 — `../../etc/passwd` 가 한 번
        저장되면 그 뒤로는 "우리 데이터"가 된다. 우리가 만든 모양이 아니면 거부한다.
        """
        if not KEY_PATTERN.match(key):
            raise ValueError(f'저장소 키의 모양이 아니다: {key!r}')
        return self._root / key

    async def save(self, chunks: AsyncIterator[bytes], *, suffix: str, max_bytes: int) -> StoredFile:
        """**임시 파일에 다 쓴 뒤 옮긴다.**

        곧바로 최종 경로에 쓰면, 상한을 넘겨 중단된 업로드가 정상 파일과 같은 자리에
        반쯤 쓰인 채로 남는다. 옮기는 것은 같은 파일시스템 안이라 원자적이다.
        """
        now = datetime.now(UTC)
        key = f'{now:%Y/%m}/{uuid.uuid4().hex}{suffix}'
        final = self._path(key)
        await asyncio.to_thread(final.parent.mkdir, parents=True, exist_ok=True)

        temp = final.with_name(f'{final.name}.part')
        size = 0
        try:
            handle = await asyncio.to_thread(temp.open, 'wb')
            try:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLargeError(max_bytes)
                    await asyncio.to_thread(handle.write, chunk)
            finally:
                await asyncio.to_thread(handle.close)
        except BaseException:
            await asyncio.to_thread(temp.unlink, True)
            raise

        await asyncio.to_thread(temp.replace, final)
        return StoredFile(key=key, size=size)

    async def read(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        handle = await asyncio.to_thread(path.open, 'rb')
        try:
            while chunk := await asyncio.to_thread(handle.read, CHUNK_SIZE):
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if not await asyncio.to_thread(path.is_file):
            return False
        await asyncio.to_thread(path.unlink)
        return True

    async def keys(self) -> list[str]:
        def _walk() -> list[str]:
            return sorted(
                str(path.relative_to(self._root).as_posix())
                for path in self._root.rglob('*')
                # `.part` 는 진행 중인 업로드다. 고아로 세면 남의 업로드를 지운다.
                if path.is_file() and not path.name.endswith('.part')
            )

        return await asyncio.to_thread(_walk)

    async def modified_at(self, key: str) -> float | None:
        path = self._path(key)

        def _mtime() -> float | None:
            return path.stat().st_mtime if path.is_file() else None

        return await asyncio.to_thread(_mtime)

    async def clear(self) -> None:
        """저장소를 통째로 비운다. **테스트와 개발용이다.**"""
        await asyncio.to_thread(shutil.rmtree, self._root, True)
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)
