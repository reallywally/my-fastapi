"""SQLite 의 기본 동작을 고쳐서 쓰는 컬럼 타입.

두 가지가 조용히 틀린다:

1. **`BIGINT PRIMARY KEY` 는 자동 증가하지 않는다.** SQLite 에서 rowid 별칭이 되는 것은
   정확히 `INTEGER PRIMARY KEY` 뿐이다. `BigInteger` 를 그대로 쓰면 INSERT 마다
   id 를 손으로 넣어야 하고, 안 넣으면 NULL 이 들어간다.
2. **`DateTime(timezone=True)` 가 naive 를 돌려준다.** SQLite 에 tz 개념이 없어서
   저장할 때 offset 이 날아간다. 읽어온 naive 와 `datetime.now(UTC)` 를 비교하면
   `TypeError` 다. 그것도 비교하는 코드에서 터진다.

나중에 Postgres 로 옮겨도 이 타입들은 그대로 둔다 — variant 가 알아서 갈린다.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Dialect, Integer, TypeDecorator

#: PK/FK 에 쓴다. SQLite 에서는 INTEGER, 그 외에는 BIGINT 로 렌더링된다.
BigIntPK = BigInteger().with_variant(Integer, 'sqlite')


class UTCDateTime(TypeDecorator[datetime]):
    """항상 aware UTC 로 주고받는다. naive 는 저장 시점에 거부한다."""

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
            # SQLite 에서 돌아오는 값. 저장할 때 UTC 로 정규화했으므로 UTC 를 붙여준다.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    """모델 기본값용. `func.now()` 를 쓰지 않는 이유는 DB 마다 tz 해석이 다르기 때문."""
    return datetime.now(UTC)
