"""모든 모델을 `METADATA` 에 등록한다. 조립이므로 bootstrap 에 있다 (§2.2).

**왜 필요한가:** alembic autogenerate 는 `METADATA` 만 본다. 모델 모듈이 import
되지 않으면 테이블이 등록되지 않고, autogenerate 는 에러가 아니라 **빈 리비전**을
만든다. 그 리비전을 커밋하면 스키마가 없는 상태로 배포되고, `alembic check` 도
통과한다 — 모델과 마이그레이션이 "둘 다 비어서" 일치하기 때문이다.

`MODELS` 는 행 dataclass 쪽 목록이다. 테이블은 있는데 그걸 읽을 모델이 없는 경우를
`tests/unit/test_model_registry.py` 가 잡는다.

새 모듈을 만들 때 여기 두 줄을 추가한다. 잊으면 그 테스트가 잡는다.
"""

from typing import Final

from app.common.db import Record
from app.modules.board.board import model as board_model
from app.modules.board.comment import model as comment_model
from app.modules.board.post import model as post_model
from app.modules.user import model as user_model

MODELS: Final[tuple[type[Record], ...]] = (
    user_model.User,
    board_model.Board,
    post_model.Post,
    comment_model.Comment,
)

__all__ = ['MODELS', 'board_model', 'comment_model', 'post_model', 'user_model']
