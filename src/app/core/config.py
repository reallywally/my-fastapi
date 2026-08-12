"""애플리케이션 설정.

읽는 것은 환경변수뿐이다. **이 모듈을 import 해도 아무 연결도 열리지 않는다** (§2.1).
모든 필드에 기본값이 있으므로 `.env` 가 없는 환경에서도 import 가 성공한다 — 유닛테스트의 전제다.
"""

from functools import lru_cache

from pydantic import RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import Environment, JournalMode
from app.core.upstream import UpstreamConfig

#: 운영에서 이 값이 그대로면 기동을 막는다.
# HS256 은 32바이트 미만 키에 경고를 낸다(RFC 7518 §3.2). 기본값도 길이는 맞춰둔다.
INSECURE_JWT_SECRET = 'change-me-in-production-0000000000'  # noqa: S105 — '비밀 아님'을 표시하는 값


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        frozen=True,
        validate_default=True,
        # UPSTREAMS__A__READ_TIMEOUT_SECONDS 형태로 개별 키를 덮어쓸 수 있게 한다.
        env_nested_delimiter='__',
    )

    environment: Environment = Environment.local
    app_name: str = 'my-fastapi'
    app_version: str = '0.1.0'
    api_prefix: str = '/api/v1'
    log_level: str = 'INFO'
    default_locale: str = 'ko'

    # --- database (§2.1: 엔진은 lifespan 이 만든다. 여기는 값만 들고 있다)
    # SQLite 를 쓴다. 드라이버 접두사가 붙어야 async 로 돈다.
    database_url: str = 'sqlite+aiosqlite:///./var/app.db'
    db_echo: bool = False
    #: SQLite 는 기본이 OFF 다. 켜지 않으면 FK 가 장식이 된다.
    db_foreign_keys: bool = True
    #: WAL 이라야 읽기가 쓰기를 막지 않는다. 쓰기는 여전히 한 번에 하나다.
    db_journal_mode: JournalMode = JournalMode.wal
    #: 쓰기 잠금 대기 시간(ms). 0 이면 경합 시 즉시 'database is locked'.
    db_busy_timeout_ms: int = 5000

    # --- redis
    redis_url: RedisDsn = 'redis://localhost:6379/0'  # type: ignore[assignment]

    # --- security (§2.2: 토큰 encode/decode 만. 도메인은 모른다)
    jwt_secret: SecretStr = SecretStr(INSECURE_JWT_SECRET)
    jwt_algorithm: str = 'HS256'
    jwt_issuer: str = 'my-fastapi'
    access_token_ttl_seconds: int = 60 * 15
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14

    # --- CORS (§0: 허용 오리진은 설정값. 하드코딩 금지)
    # 환경변수에는 JSON 배열로 넣는다: CORS_ALLOW_ORIGINS=["http://localhost:3000"]
    cors_allow_origins: tuple[str, ...] = ()
    cors_allow_credentials: bool = True

    # --- 외부 서버 (§5). 이름 → 설정. 새 서버는 설정 한 줄이고 코드 변경이 아니다.
    #   UPSTREAMS={"a":{"base_url":"https://a.example.com"},"b":{"base_url":"..."}}
    # 개별 키만 덮어쓸 수도 있다: UPSTREAMS__A__READ_TIMEOUT_SECONDS=10
    upstreams: dict[str, UpstreamConfig] = {}

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.production

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith('sqlite')

    @property
    def redis_dsn(self) -> str:
        return str(self.redis_url)

    @field_validator('database_url')
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """동기 드라이버를 넣으면 기동은 되고 첫 쿼리에서 죽는다. 여기서 막는다."""
        if '+' not in value.split('://', 1)[0]:
            raise ValueError(f'async 드라이버를 명시해라 (예: sqlite+aiosqlite://). 받은 값: {value!r}')
        return value

    @model_validator(mode='after')
    def _guard_production(self) -> 'Settings':
        if self.is_production:
            if '*' in self.cors_allow_origins:
                raise ValueError('production 에서 CORS 와일드카드는 허용하지 않는다')
            if self.db_echo:
                raise ValueError('production 에서 db_echo 는 켤 수 없다')
            if self.jwt_secret.get_secret_value() == INSECURE_JWT_SECRET:
                raise ValueError('production 에서 기본 jwt_secret 을 그대로 쓸 수 없다')
            insecure = sorted(name for name, up in self.upstreams.items() if not up.verify_tls)
            if insecure:
                raise ValueError(f'production 에서 TLS 검증을 끌 수 없다: {insecure}')
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스당 한 번만 읽는다. 테스트에서는 `get_settings.cache_clear()` 로 리셋한다."""
    return Settings()
