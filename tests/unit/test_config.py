"""설정 계층. DB 없이 밀리초 단위로 돈다."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.constants import Environment


def test_defaults_allow_import_without_env():
    settings = Settings()

    assert settings.environment is Environment.local
    assert settings.database_dsn.startswith('postgresql+asyncpg://')
    assert settings.redis_dsn.startswith('redis://')
    assert settings.cors_allow_origins == ()


def test_production_rejects_cors_wildcard():
    """§0 — 허용 오리진은 설정값이되, 운영에서 와일드카드는 막는다."""
    with pytest.raises(ValidationError, match='와일드카드'):
        Settings(environment=Environment.production, cors_allow_origins=('*',))


def test_production_rejects_sql_echo():
    with pytest.raises(ValidationError, match='db_echo'):
        Settings(environment=Environment.production, db_echo=True)


def test_local_allows_wildcard():
    settings = Settings(environment=Environment.local, cors_allow_origins=('*',))

    assert settings.cors_allow_origins == ('*',)
    assert settings.is_production is False


def test_settings_are_frozen():
    settings = Settings()

    with pytest.raises(ValidationError):
        settings.app_name = 'other'
