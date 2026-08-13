"""§8 규칙표 중 "코드리뷰/grep" 으로 되어 있던 항목을 테스트로 옮긴 것.

**AST 로 검사한다.** 문자열 검색은 주석과 독스트링을 위반으로 잡는다 — 이 문서화된
코드베이스에서는 그게 곧 오탐이다. 판단 대상은 실제로 실행되는 호출과 시그니처뿐이다.

지금은 대부분 공허하게 통과한다 (`modules/` 가 비어 있으니까). 그게 요점이다 —
Phase 3 에서 첫 모듈이 들어오는 순간부터 이 테스트들이 일하기 시작한다.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.paths import APP_DIR

SOURCE_FILES = sorted(APP_DIR.rglob('*.py'))
SERVICE_AND_REPOSITORY_FILES = [p for p in SOURCE_FILES if p.name in {'service.py', 'repository.py'}]
#: 깊이를 고정하지 않는다 — `modules/board/post/schema.py` 처럼 컨텍스트 안에 모듈이
#: 중첩된다 (§4.1). `common/db/schema.py` 는 이름만 같고 테이블 컬럼 팩토리라 빠진다.
MODULE_SCHEMA_FILES = [p for p in SOURCE_FILES if p.name == 'schema.py' and 'modules' in p.relative_to(APP_DIR).parts]

HTTP_OBJECTS = {'Request', 'Response', 'UploadFile', 'WebSocket', 'BackgroundTasks'}
DTO_SUFFIXES = ('Request', 'Response')
IO_RESOURCE_FACTORIES = {
    'create_async_engine',
    'create_engine',
    'Redis.from_url',
    'ConnectionPool.from_url',
    'httpx.AsyncClient',
    'AsyncClient',
}


def _name(path: Path) -> str:
    return str(path.relative_to(APP_DIR))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


def _dotted(node: ast.expr) -> str:
    """`Redis.from_url` 같은 점 표기를 문자열로 되살린다."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return '.'.join(reversed(parts))


def _called_names(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield _dotted(node.func)


def _module_level_called_names(tree: ast.Module) -> Iterator[str]:
    """함수·클래스 본문으로는 내려가지 않는다 — import 시점에 실행되는 것만 본다."""
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(node, ast.Call):
            yield _dotted(node.func)
        stack.extend(ast.iter_child_nodes(node))


def _offenders(paths: list[Path], predicate) -> list[str]:
    return [_name(path) for path in paths if predicate(_tree(path))]


def test_rule_4_no_create_all_in_app_code():
    """규칙 #4 — 스키마의 유일한 소스는 마이그레이션이다 (§2.3)."""
    offenders = _offenders(
        SOURCE_FILES,
        lambda tree: any(name.split('.')[-1] == 'create_all' for name in _called_names(tree)),
    )
    assert not offenders, f'create_all() 을 호출하는 앱 코드가 있다: {offenders}'


def test_rule_no_sys_exit_in_app_code():
    """§3.3 — 라이브러리·모듈 코드는 예외만 올린다. 종료는 프로세스 관리자의 몫이다."""
    banned = {'sys.exit', 'exit', 'quit', 'os._exit'}
    offenders = _offenders(SOURCE_FILES, lambda tree: any(name in banned for name in _called_names(tree)))
    assert not offenders, f'프로세스를 직접 죽이는 앱 코드가 있다: {offenders}'


def test_rule_3_no_io_resources_at_module_scope():
    """규칙 #3 — I/O 자원은 lifespan 안에서만 만든다 (§2.1).

    FBA 는 모듈 최상단에서 엔진을 만들었고, 그래서 `import` 만으로 DB 설정이
    유효해야 했다. 여기서는 그게 구조적으로 불가능해야 한다.
    """
    offenders = _offenders(
        SOURCE_FILES,
        lambda tree: any(name in IO_RESOURCE_FACTORIES for name in _module_level_called_names(tree)),
    )
    assert not offenders, f'모듈 최상위에서 I/O 자원을 만든다: {offenders}'


def test_rule_17_only_one_module_creates_engines():
    """규칙 #17 — 엔진 생성은 `common/db/engine.py` 하나뿐 (§1.6).

    SQLite 는 PRAGMA 를 안 걸면 조용히 틀린다. `create_async_engine` 을 여기저기서
    부르면 그중 하나는 반드시 FK 가 꺼진 채로 돈다. 실제로 `migrations/env.py` 가
    그렇게 만들어져서 새 체크아웃에서 `alembic upgrade` 가 실패했다.
    """
    allowed = {'common/db/engine.py'}
    offenders = _offenders(
        [path for path in SOURCE_FILES if _name(path) not in allowed],
        lambda tree: any(name.split('.')[-1] == 'create_async_engine' for name in _called_names(tree)),
    )
    assert not offenders, f'engine.py 밖에서 엔진을 만든다: {offenders}'


def test_rule_25_only_one_module_creates_http_clients():
    """규칙 #25 — `httpx.AsyncClient` 는 `common/http/registry.py` 에서만 만든다 (§5).

    타임아웃 4종과 커넥션 상한이 걸린 클라이언트는 그 파일에서만 나온다. 다른 곳에서
    `AsyncClient()` 를 직접 만들면 httpx 기본값을 쓰게 되고, **pool 대기가 무제한**이라
    느린 업스트림 앞에 우리 요청이 무한정 줄을 선다.
    """
    allowed = {'common/http/registry.py'}
    offenders = _offenders(
        [path for path in SOURCE_FILES if _name(path) not in allowed],
        lambda tree: any(name.split('.')[-1] == 'AsyncClient' for name in _called_names(tree)),
    )
    assert not offenders, f'registry.py 밖에서 httpx 클라이언트를 만든다: {offenders}'


def test_rule_26_wire_dtos_stay_inside_their_gateway():
    """규칙 #26 — 업스트림 응답 DTO 는 `gateway.py` 밖으로 나가지 않는다 (§5).

    밖으로 나가면 상대의 필드명이 우리 API 계약이나 테이블 스키마가 된다. wire DTO 는
    밑줄로 시작하는 이름으로 두고, 다른 파일이 그걸 import 하지 않는지 검사한다.
    """
    offenders: list[str] = []
    for path in SOURCE_FILES:
        if path.name == 'gateway.py':
            continue
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith('gateway'):
                private = [alias.name for alias in node.names if alias.name.startswith('_')]
                if private:
                    offenders.append(f'{_name(path)} → {private}')
    assert not offenders, f'wire DTO 가 gateway 밖에서 쓰인다: {offenders}'


def _dto_names(tree: ast.Module) -> Iterator[str]:
    """`schema.py` 의 최상위 DTO 이름.

    `BaseModel` 을 직접 상속한 것만 보면 `class UpdateUserRequest(CreateUserRequest)` 를
    놓친다. 파일 순서대로 훑으면서 앞서 DTO 로 판정된 이름을 상속한 것도 DTO 로 친다 —
    Python 은 정의된 뒤에만 상속할 수 있으므로 한 번의 순회로 충분하다.
    """
    dtos: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {_dotted(base).split('.')[-1] for base in node.bases}
        if 'BaseModel' in bases or bases & dtos:
            dtos.add(node.name)
            yield node.name


@pytest.mark.skipif(not MODULE_SCHEMA_FILES, reason='아직 modules/*/schema.py 가 없다 (Phase 3~)')
def test_rule_32_module_dtos_are_named_by_direction():
    """규칙 #32 — 모듈 DTO 는 `~Request` / `~Response` 로 끝난다 (§1.2).

    이름만 보고 방향을 알 수 있어야 한다. `UserOut` 은 들어오는 것인지 나가는 것인지
    이름이 말해주지 않는다 — 알려면 라우터를 열어봐야 한다.

    조각(다른 DTO 안에만 들어가는 모델)과 공통 베이스는 밑줄로 시작하는 이름으로 둔다.
    규칙 #26 의 wire DTO 와 같은 표시다 — "이건 단독으로 오가는 계약이 아니다".
    """
    offenders = [
        f'{_name(path)}::{dto}'
        for path in MODULE_SCHEMA_FILES
        for dto in _dto_names(_tree(path))
        if not dto.startswith('_') and not dto.endswith(DTO_SUFFIXES)
    ]
    assert not offenders, f'DTO 이름이 방향을 말하지 않는다 — ~Request / ~Response 로 끝나야 한다: {offenders}'


def test_rule_18_dialect_specifics_do_not_leak_into_modules():
    """규칙 #18 — 방언 전용 처리는 `db/engine.py`·`db/types.py` 안에 가둔다 (§1.6).

    PostgreSQL·MySQL 로 옮길 때 고쳐야 할 파일이 몇 개인지가 여기서 결정된다.
    새는 통로는 둘뿐이다:

    - `sqlalchemy.dialects.*` import — 대놓고 방언에 묶인 타입·구문
    - `text()` — 원시 SQL 문자열. Core 표현식과 달리 방언별로 컴파일되지 않는다

    **AST 로 본다.** 문서에서 방언 이름을 언급하는 것은 위반이 아니다 — 오히려
    왜 그렇게 짰는지 설명하는 자리다.
    """
    module_files = [path for path in SOURCE_FILES if _name(path).startswith('modules/')]

    def _leaks(tree: ast.Module) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('sqlalchemy.dialects'):
                return True
            if isinstance(node, ast.Import) and any(a.name.startswith('sqlalchemy.dialects') for a in node.names):
                return True
        return any(name.split('.')[-1] == 'text' for name in _called_names(tree))

    offenders = _offenders(module_files, _leaks)
    assert not offenders, f'modules/ 안에 방언 전용 코드가 있다: {offenders}'


def test_rule_6_soft_delete_condition_is_never_written_by_hand():
    """규칙 #6 — `deleted == 0` 은 `common/db/sql.py` 의 `alive()` 에만 있다 (§2.4).

    FBA 는 이 조건을 106곳에 손으로 붙였고 14곳을 빠뜨렸다. ORM 전역 필터가 없는
    지금, 조각이 한 곳에만 있다는 사실이 그 자리를 대신한다.

    비교식(`c.deleted == 0`)을 AST 로 찾는다 — 독스트링에서 규칙을 설명하는 문장을
    위반으로 잡으면 그건 규칙이 아니라 함정이다.
    """

    def _compares_deleted(tree: ast.Module) -> bool:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            left = node.left
            if isinstance(left, ast.Attribute) and left.attr == 'deleted':
                return True
            if (
                isinstance(left, ast.Subscript)
                and isinstance(left.slice, ast.Constant)
                and left.slice.value == 'deleted'
            ):
                return True
        return False

    offenders = _offenders(SERVICE_AND_REPOSITORY_FILES, _compares_deleted)
    assert not offenders, f'soft delete 조건을 손으로 쓴다 — alive() 를 써라: {offenders}'


@pytest.mark.skipif(not SERVICE_AND_REPOSITORY_FILES, reason='아직 service/repository 가 없다 (Phase 3~)')
def test_rule_1_no_commit_in_service_or_repository():
    """규칙 #1 — 트랜잭션은 DI 가 결정한다. 서비스/레포는 flush() 만 (§1.1)."""
    offenders = _offenders(
        SERVICE_AND_REPOSITORY_FILES,
        lambda tree: any(name.split('.')[-1] == 'commit' for name in _called_names(tree)),
    )
    assert not offenders, f'service/repository 에서 commit() 을 호출한다: {offenders}'


@pytest.mark.skipif(not SERVICE_AND_REPOSITORY_FILES, reason='아직 service 가 없다 (Phase 3~)')
def test_rule_5_services_do_not_accept_http_objects():
    """규칙 #5 — 서비스 시그니처에 Request/Response/UploadFile 금지 (§2.7).

    이 누수가 FBA 에서 실제 권한 버그를 만들었다 (§4.6 참조).
    """

    def _leaks(tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                if arg.annotation is not None and _dotted(arg.annotation).split('.')[-1] in HTTP_OBJECTS:
                    return True
        return False

    offenders = _offenders(SERVICE_AND_REPOSITORY_FILES, _leaks)
    assert not offenders, f'서비스가 HTTP 객체를 받는다: {offenders}'


def test_rule_10_file_length_limit():
    """규칙 #10 — 400줄 상한 (§2.9)."""
    offenders = [
        (_name(path), len(path.read_text(encoding='utf-8').splitlines()))
        for path in SOURCE_FILES
        if len(path.read_text(encoding='utf-8').splitlines()) > 400
    ]
    assert not offenders, f'400줄을 넘는 파일이 있다: {offenders}'
