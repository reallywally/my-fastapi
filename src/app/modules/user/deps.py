"""요청 주체 확인.

**여기는 Phase 5 의 자리를 잡아둔 것이다.** 토큰 검증과 사용자 로드는 `modules/auth/deps.py`
가 맡는다 (§2.2 해결 2: `common/security/token.py` 는 encode/decode 만 안다).

지금은 **401 을 낸다.** 가짜 주체를 넣어두면 인가가 걸린 척하는 엔드포인트가 되고,
그게 Phase 5 까지 살아남으면 그대로 구멍이다. 401 은 안전한 쪽으로 틀린다.

라우트를 지금 노출해두는 이유는 §0 이다 — OpenAPI 계약이 확정되어야 화면 작업을
병행할 수 있다.
"""

from typing import Annotated

from fastapi import Depends

from app.common.errors import UnauthorizedError
from app.common.security import Principal


async def get_principal() -> Principal:
    raise UnauthorizedError(code='auth.unauthorized')


PrincipalDep = Annotated[Principal, Depends(get_principal)]
