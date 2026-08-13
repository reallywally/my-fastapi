"""행(row)을 담는 dataclass 의 공통 조상 (§1.2, §1.6).

**매핑이 아니라 모양이다.** 테이블의 정의는 `Table` 이고 (`schema.py`), 이 클래스는
`SELECT` 가 무엇을 읽어오는지와 서비스가 무엇을 받는지를 정한다. 둘을 잇는 것은
`sql.columns()` 이고, 어긋나면 그 자리에서 `KeyError` 로 죽는다.

ORM 행 객체와 다른 점 하나: **이 객체를 고쳐도 DB 는 모른다.** 수정은 반드시
레포지토리를 거친다. unit of work 를 잃은 대가이자, 동시에 "언제 SQL 이 나가는지가
코드에 보인다" 는 이득이다.

`slots=True` 를 쓴다. 행 객체는 요청당 수백 개가 만들어지고, 오타로 없는 속성을
대입하는 사고(`user.emial = ...`)를 런타임에 막아준다.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Table


@dataclass(slots=True)
class Record:
    """모든 행의 공통 필드. 필드 순서는 상관없다 — 이름으로 채운다."""

    #: 이 행이 사는 테이블. `sql.py` 의 헬퍼가 전부 여기서 출발한다.
    TABLE: ClassVar[Table]

    id: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class SoftDeletable(Record):
    """`deleted` 를 가진 행 (§1.4).

    이 클래스를 상속했다는 사실 자체가 규약이다 — 조회에 `alive()` 가 붙어야 하고,
    unique 제약에 `deleted` 가 포함되어야 한다.
    """

    deleted: int

    @property
    def is_deleted(self) -> bool:
        return self.deleted != 0
