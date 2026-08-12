"""모든 모델을 `Base.metadata` 에 등록한다. 조립이므로 bootstrap 에 있다 (§2.2).

**왜 필요한가:** alembic autogenerate 는 `Base.metadata` 만 본다. 모델 모듈이 import
되지 않으면 테이블이 등록되지 않고, autogenerate 는 에러가 아니라 **빈 리비전**을
만든다. 그 리비전을 커밋하면 스키마가 없는 상태로 배포되고, `alembic check` 도
통과한다 — 모델과 마이그레이션이 "둘 다 비어서" 일치하기 때문이다.

새 모듈을 만들 때 여기 한 줄을 추가한다. 잊으면
`tests/unit/test_model_registry.py` 가 잡는다.
"""

from app.modules.user import model as user_model

__all__ = ['user_model']
