"""방언 차이를 흡수하는 컬럼 타입 (§1.6).

**이 파일이 이식성의 절반이다.** 나머지 절반은 `engine.py`. 두 파일 밖으로 방언 이름이
새어나가면 잘못 짠 것이다 (규칙 #18).

세 가지가 방언마다 조용히 다르다:

1. **자동 증가 PK.** SQLite 에서 rowid 별칭이 되는 것은 정확히 `INTEGER PRIMARY KEY`
   뿐이다. `BigInteger` 를 그대로 쓰면 INSERT 마다 id 를 손으로 넣어야 하고, 안 넣으면
   NULL 이 들어간다. PostgreSQL·MySQL 은 `BIGINT` 그대로 자동 증가한다.
2. **timezone.** SQLite 에는 tz 개념이 없어서 offset 이 날아가고, MySQL 의 `DATETIME`
   도 마찬가지다. PostgreSQL 의 `timestamptz` 만 tz 를 기억한다. 읽어온 naive 와
   `datetime.now(UTC)` 를 비교하면 `TypeError` — 그것도 비교하는 코드에서 터진다.
3. **문자열 길이.** MySQL 은 인덱스가 걸리는 컬럼에 길이가 **필수**다. 그래서 이
   프로젝트의 `String` 은 언제나 길이를 준다 (`String(50)`, 절대 그냥 `String`).

1·2 는 아래 두 타입이 처리한다. 3 은 규칙이라 사람이 지켜야 하고, `model.py` 를
읽을 때 눈에 보이게 두는 편이 낫다고 판단했다.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Dialect, Integer, TypeDecorator

#: PK/FK 에 쓴다. SQLite 에서는 INTEGER, 그 외에는 BIGINT 로 렌더링된다.
BigIntPK = BigInteger().with_variant(Integer, 'sqlite')


class UTCDateTime(TypeDecorator[datetime]):
    """항상 aware UTC 로 주고받는다. naive 는 저장 시점에 거부한다.

    `timestamptz` 가 있는 PostgreSQL 에서는 사실상 통과 계층이고, tz 를 버리는
    SQLite·MySQL 에서는 저장 전 UTC 정규화 + 읽은 뒤 UTC 부착을 담당한다.
    **어느 방언에서든 앱 코드가 보는 값은 같다** — 그게 이 타입이 있는 이유다.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError('naive datetime 은 저장하지 않는다 — tz 를 붙여라 (datetime.now(UTC))')
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # tz 를 버리는 방언에서 돌아온 값. 저장할 때 UTC 로 정규화했으므로 UTC 를 붙여준다.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    """모델 기본값용. `func.now()` 를 쓰지 않는 이유는 DB 마다 tz 해석이 다르기 때문."""
    return datetime.now(UTC)
