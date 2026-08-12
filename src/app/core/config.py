"""애플리케이션 설정.

읽는 것은 환경변수뿐이다. **이 모듈을 import 해도 아무 연결도 열리지 않는다** (§2.1).
모든 필드에 기본값이 있으므로 `.env` 가 없는 환경에서도 import 가 성공한다 — 유닛테스트의 전제다.
"""

from functools import lru_cache

from pydantic import PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import Environment


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        frozen=True,
        validate_default=True,
    )

    environment: Environment = Environment.local
    app_name: str = 'my-fastapi'
    app_version: str = '0.1.0'
    api_prefix: str = '/api/v1'
    log_level: str = 'INFO'

    # --- database (§2.1: 엔진은 lifespan 이 만든다. 여기는 값만 들고 있다)
    database_url: PostgresDsn = 'postgresql+asyncpg://app:app@localhost:5432/app'  # type: ignore[assignment]
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 3600
    db_pool_pre_ping: bool = True
    db_echo: bool = False

    # --- redis
    redis_url: RedisDsn = 'redis://localhost:6379/0'  # type: ignore[assignment]

    # --- CORS (§0: 허용 오리진은 설정값. 하드코딩 금지)
    # 환경변수에는 JSON 배열로 넣는다: CORS_ALLOW_ORIGINS=["http://localhost:3000"]
    cors_allow_origins: tuple[str, ...] = ()
    cors_allow_credentials: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.production

    @property
    def database_dsn(self) -> str:
        return str(self.database_url)

    @property
    def redis_dsn(self) -> str:
        return str(self.redis_url)

    @model_validator(mode='after')
    def _guard_production(self) -> 'Settings':
        if self.is_production:
            if '*' in self.cors_allow_origins:
                raise ValueError('production 에서 CORS 와일드카드는 허용하지 않는다')
            if self.db_echo:
                raise ValueError('production 에서 db_echo 는 켤 수 없다')
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 읽는다. 테스트에서는 `get_settings.cache_clear()` 로 리셋한다."""
    return Settings()
