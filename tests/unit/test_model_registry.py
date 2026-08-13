"""테이블이 `METADATA` 에 실제로 등록되는지, 그리고 그걸 읽을 모델이 있는지 (§2.3).

이 테스트가 없으면 다음이 조용히 통과한다:
1. 새 모듈에 `model.py` 를 만든다
2. `bootstrap/models.py` 에 추가하는 것을 잊는다
3. `alembic revision --autogenerate` 가 **빈 리비전**을 만든다
4. `alembic check` 도 통과한다 — 모델과 마이그레이션이 "둘 다 비어서" 일치하니까
5. 배포하면 테이블이 없다

실제로 Phase 3 에서 이 순서로 빈 리비전이 나왔다.
"""

import importlib
from dataclasses import fields

from app.bootstrap import models
from app.common.db import METADATA, SoftDeletable
from app.core.paths import APP_DIR

MODEL_MODULES = sorted(
    'app.' + str(path.relative_to(APP_DIR).with_suffix('')).replace('/', '.')
    for path in (APP_DIR / 'modules').rglob('model.py')
)


def test_there_is_at_least_one_model():
    """Phase 3 부터는 모델이 있어야 한다. 0개면 아래 검사가 공허하게 통과한다."""
    assert MODEL_MODULES, 'modules/ 안에 model.py 가 없다'
    assert models.MODELS, 'bootstrap/models.py 의 MODELS 가 비어 있다'


def test_every_model_module_is_registered_via_bootstrap():
    """`bootstrap/models.py` 만 import 해도 모든 테이블이 metadata 에 올라와야 한다."""
    registered = set(METADATA.tables)

    missing: list[str] = []
    for dotted in MODEL_MODULES:
        module = importlib.import_module(dotted)
        for attr in vars(module).values():
            name = getattr(attr, 'name', None)
            if getattr(attr, '__class__', None).__name__ == 'Table' and name not in registered:
                missing.append(f'{dotted}:{name}')

    assert not missing, f'bootstrap/models.py 에 빠진 모델이 있다: {missing}'


def test_every_table_has_a_model_that_can_read_it():
    """테이블은 있는데 행 dataclass 가 없으면 그 테이블은 아무도 읽지 못한다 (§1.6)."""
    covered = {model.TABLE.name for model in models.MODELS}
    orphans = set(METADATA.tables) - covered

    assert not orphans, f'MODELS 에 등록되지 않은 테이블이 있다: {sorted(orphans)}'


def test_model_fields_match_their_table_columns():
    """dataclass 필드와 컬럼이 어긋나면 그 테이블을 읽는 **모든 요청**이 죽는다.

    `sql.columns()` 가 필드 이름으로 컬럼을 찾기 때문이다 (§1.6).
    """
    mismatched: list[str] = []
    for model in models.MODELS:
        names = {field.name for field in fields(model)}
        columns = set(model.TABLE.c.keys())
        if names != columns:
            mismatched.append(
                f'{model.__name__}: 모델에만 {sorted(names - columns)}, 테이블에만 {sorted(columns - names)}'
            )

    assert not mismatched, f'모델과 테이블이 어긋난다: {mismatched}'


def test_soft_deletable_tables_scope_their_unique_constraints():
    """§1.4 — soft delete 테이블의 unique 는 `deleted` 를 포함해야 한다.

    빠뜨리면 탈퇴한 아이디를 영구히 재사용할 수 없다. 테이블이 늘어날수록
    잊기 쉬운 규칙이라 여기서 기계로 검사한다.
    """
    offenders: list[str] = []
    for model in models.MODELS:
        if not issubclass(model, SoftDeletable):
            continue
        for constraint in model.TABLE.constraints:
            columns = {column.name for column in getattr(constraint, 'columns', [])}
            is_unique = type(constraint).__name__ == 'UniqueConstraint'
            if is_unique and 'deleted' not in columns:
                offenders.append(f'{model.TABLE.name}({", ".join(sorted(columns))})')

    assert not offenders, f'soft delete 테이블의 unique 에 deleted 가 없다: {offenders}'
