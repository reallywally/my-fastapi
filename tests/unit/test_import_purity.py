"""§2.1 / 규칙 #3 — import 만으로는 아무 자원도 생기지 않는다.

FBA 는 `import` 시점에 엔진을 만들고 실패하면 `sys.exit()` 했다. 그래서 유닛테스트가
불가능했다. 이 테스트가 그 회귀를 막는다.

**서브프로세스에서 돌린다.** 같은 프로세스에서는 다른 테스트가 이미 설정을 캐시했을 수
있어서 "환경변수 없이도 import 된다"를 증명할 수 없다.
"""

import subprocess
import sys
import textwrap

from app.core.paths import PROJECT_ROOT

#: DB/Redis 관련 환경변수를 모두 지운 채로 돌린다.
_CLEAN_ENV = {
    'PATH': '/usr/bin:/bin:/usr/local/bin',
    'PYTHONPATH': str(PROJECT_ROOT / 'src'),
    'HOME': '/tmp',  # noqa: S108  — .env 를 우연히 집지 않게 격리
}


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-c', textwrap.dedent(script)],
        check=False,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT / 'src',  # 프로젝트 루트의 .env 를 읽지 않도록
        env=_CLEAN_ENV,
    )


def test_importing_app_opens_no_connections():
    result = _run("""
        import app.main

        state = app.main.app.state
        assert not hasattr(state, 'engine'), 'import 만으로 엔진이 생겼다'
        assert not hasattr(state, 'redis'), 'import 만으로 redis 가 생겼다'
        assert not hasattr(state, 'session_factory'), 'import 만으로 세션 팩토리가 생겼다'
    """)
    assert result.returncode == 0, result.stderr


def test_importing_config_without_env_succeeds():
    """DATABASE_URL 이 없어도 import 와 설정 로딩이 성공해야 한다."""
    result = _run("""
        from app.core.config import get_settings

        settings = get_settings()
        assert settings.database_url.startswith('sqlite+aiosqlite://')
    """)
    assert result.returncode == 0, result.stderr


def test_creating_app_opens_no_connections():
    """`create_app()` 도 자원을 만들지 않는다 — 그래서 테스트가 state 를 가짜로 채울 수 있다."""
    result = _run("""
        from app.bootstrap.app import create_app

        application = create_app()
        assert not hasattr(application.state, 'engine')
        assert not hasattr(application.state, 'redis')
    """)
    assert result.returncode == 0, result.stderr
