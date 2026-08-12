"""요청을 수행하는 주체.

`User` 모델이 아니다 — **인가에 필요한 최소한**만 담는다. 그래서 `common` 에 있어도
§2.2 의 "common 은 도메인을 모른다" 를 위반하지 않는다.

이 타입이 있어서 `modules/auth`(발급자)와 `modules/user`(소비자)가 서로를 import 하지
않는다. FBA 에서 `common/security/jwt.py` 가 `User` 모델을 끌어다 쓰면서 순환 참조가
생긴 지점이 바로 여기였다.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    id: int
    is_superuser: bool = False

    def can_act_on(self, owner_id: int) -> bool:
        """본인이거나 관리자. §4.6 의 소유권 규칙을 한 곳에서 표현한다.

        비교 대상은 **호출자가 넘긴 owner_id** 다. §2.7 의 FBA 버그는 조회한 행의
        `id` 와 비교해서 조건이 상수가 된 경우였다.
        """
        return self.is_superuser or self.id == owner_id
