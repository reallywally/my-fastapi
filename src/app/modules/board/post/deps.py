"""요청 스코프 횡단 관심사 (§1.3).

여기 있는 것은 **뷰어 식별자** 하나다. 조회수 중복 판정(§4.5)에 필요한데, 그 값을
서비스가 직접 만들 수는 없다 — 만들려면 `Request` 를 받아야 하고 그건 §2.7 이 막는
바로 그 누수다. 그래서 라우터가 `Depends` 로 받아 원시 문자열로 넘긴다.
"""

import hashlib
from typing import Annotated

from fastapi import Depends, Request

#: 키에 넣을 길이. 충돌해봐야 두 뷰어가 10분 동안 한 번 덜 세는 것뿐이라 16자면 충분하다.
VIEWER_KEY_LENGTH = 16


def viewer_key(request: Request) -> str:
    """조회 중복 판정용 뷰어 식별자.

    **원본 IP 를 Redis 키에 넣지 않는다.** 조회수를 세자고 접속 주소를 10분씩 보관할
    이유가 없다. 해시는 되돌릴 수 없고, 우리에게 필요한 것은 "같은 뷰어인가" 뿐이다.

    주체가 생기면(Phase 5) 로그인한 사용자는 `user:{id}` 로 세는 편이 정확하다.
    지금은 주체가 없으니 접속 정보로만 판정한다.

    프록시 뒤에서는 `request.client` 가 프록시를 가리킨다. `X-Forwarded-For` 를 믿는
    것은 배포 환경(신뢰 프록시 목록)을 알아야 하는 문제라 Phase 6 로 미룬다 — 지금
    틀리는 방향은 "덜 센다" 쪽이고, 그건 안전한 쪽이다.
    """
    client = request.client.host if request.client else 'unknown'
    agent = request.headers.get('user-agent', '')
    digest = hashlib.sha256(f'{client}|{agent}'.encode()).hexdigest()
    return digest[:VIEWER_KEY_LENGTH]


ViewerKeyDep = Annotated[str, Depends(viewer_key)]
