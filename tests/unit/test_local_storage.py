"""로컬 저장소 구현 (§4.9).

**저장 파일명은 저장소가 정한다.** 원본 이름을 경로에 쓰면 `../../etc/passwd` 가
경로가 되고, 한 번 저장되면 그 뒤로는 "우리 데이터"가 된다. 그리고 **크기는 쓰면서
잰다** — 다 받은 뒤에 재는 상한은 디스크를 지켜주지 못한다.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.common.storage import FileTooLargeError, LocalStorage


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / 'uploads')


async def test_save_returns_a_generated_key(storage):
    stored = await storage.save(_chunks(b'hello'), suffix='.txt', max_bytes=1024)

    assert stored.size == 5
    assert stored.key.endswith('.txt')
    assert 'hello' not in stored.key


async def test_saved_bytes_come_back(storage):
    stored = await storage.save(_chunks(b'hello ', b'world'), suffix='.txt', max_bytes=1024)

    read = b''.join([chunk async for chunk in storage.read(stored.key)])

    assert read == b'hello world'


async def test_two_saves_never_collide(storage):
    first = await storage.save(_chunks(b'a'), suffix='.txt', max_bytes=1024)
    second = await storage.save(_chunks(b'b'), suffix='.txt', max_bytes=1024)

    assert first.key != second.key


async def test_oversized_upload_is_cut_off(storage):
    """상한을 넘기면 그 자리에서 끊는다. 전부 받은 뒤에 재지 않는다."""
    with pytest.raises(FileTooLargeError):
        await storage.save(_chunks(b'x' * 10, b'x' * 10), suffix='.txt', max_bytes=15)


async def test_a_rejected_upload_leaves_nothing_behind(storage):
    """반쯤 쓰인 파일이 정상 파일과 같은 자리에 남으면 안 된다."""
    with pytest.raises(FileTooLargeError):
        await storage.save(_chunks(b'x' * 100), suffix='.txt', max_bytes=10)

    assert await storage.keys() == []


async def test_keys_lists_what_was_saved(storage):
    stored = await storage.save(_chunks(b'a'), suffix='.txt', max_bytes=1024)

    assert await storage.keys() == [stored.key]


async def test_delete_is_idempotent(storage):
    stored = await storage.save(_chunks(b'a'), suffix='.txt', max_bytes=1024)

    assert await storage.delete(stored.key) is True
    assert await storage.delete(stored.key) is False


async def test_reading_a_missing_key_raises(storage):
    missing = '2026/08/' + '0' * 32 + '.txt'

    with pytest.raises(FileNotFoundError):
        [chunk async for chunk in storage.read(missing)]


@pytest.mark.parametrize(
    'key',
    ['../../etc/passwd', '/etc/passwd', '2026/08/../../../etc/passwd', 'passwd'],
)
async def test_keys_outside_our_shape_are_refused(storage, key):
    """키는 DB 에서 오지만 그렇다고 믿을 이유는 없다."""
    with pytest.raises(ValueError, match='저장소 키'):
        await storage.delete(key)


async def test_modified_at_is_none_for_a_missing_key(storage):
    assert await storage.modified_at('2026/08/' + '0' * 32 + '.txt') is None
