"""세션 의존성 중 DB 가 필요 없는 부분. 실제 트랜잭션 동작은 integration 쪽에 있다."""

import pytest

from app.common.db.session import get_db, get_db_tx, get_session_factory


class _RequestWithoutState:
    """lifespan 이 돌지 않은 앱."""

    app = type('App', (), {'state': type('State', (), {})()})()


def test_missing_session_factory_raises():
    """§3.3 — 자원이 없으면 예외지, 프로세스 종료가 아니다."""
    with pytest.raises(RuntimeError, match='lifespan'):
        get_session_factory(_RequestWithoutState())


@pytest.mark.parametrize('dependency', [get_db, get_db_tx])
async def test_dependencies_surface_the_same_error(dependency):
    with pytest.raises(RuntimeError, match='lifespan'):
        async for _ in dependency(_RequestWithoutState()):
            pass
