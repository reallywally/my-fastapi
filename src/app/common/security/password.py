"""비밀번호 해싱. 도메인을 모른다 — 문자열만 받는다.

Argon2id 를 쓴다. bcrypt 는 72바이트에서 입력이 조용히 잘려서 긴 비밀번호의 뒷부분이
사실상 무시된다.

`verify_and_update` 를 같이 노출하는 이유: 파라미터를 올리고 나면 기존 해시를 갱신할
지점이 필요한데, 로그인 성공 시점이 평문을 가진 유일한 순간이다 (Phase 4 에서 쓴다).
"""

from pwdlib import PasswordHash

#: 프로세스당 하나. I/O 자원이 아니라 순수 계산기라 §2.1 의 대상이 아니다.
_hasher = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """틀린 비밀번호와 손상된 해시를 구분하지 않는다 — 호출자에게는 둘 다 '실패'다."""
    try:
        return _hasher.verify(plain, hashed)
    except Exception:
        return False


def verify_and_upgrade(plain: str, hashed: str) -> tuple[bool, str | None]:
    """(검증 결과, 갱신된 해시 또는 None).

    두 번째 값이 None 이 아니면 저장된 해시가 낡았다는 뜻이다. 저장은 호출자가 한다 —
    이 모듈은 DB 를 모른다.
    """
    try:
        return _hasher.verify_and_update(plain, hashed)
    except Exception:
        return False, None
