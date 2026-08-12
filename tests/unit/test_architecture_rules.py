"""§7 규칙표 중 "코드리뷰/grep" 으로 되어 있던 항목을 테스트로 옮긴 것.

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

HTTP_OBJECTS = {'Request', 'Response', 'UploadFile', 'WebSocket', 'BackgroundTasks'}
IO_RESOURCE_FACTORIES = {'create_async_engine', 'create_engine', 'Redis.from_url', 'ConnectionPool.from_url'}


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
