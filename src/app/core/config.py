"""애플리케이션 설정.

읽는 것은 환경변수뿐이다. **이 모듈을 import 해도 아무 연결도 열리지 않는다** (§2.1).
모든 필드에 기본값이 있으므로 `.env` 가 없는 환경에서도 import 가 성공한다 — 유닛테스트의 전제다.
"""

from functools import lru_cache
from typing import Final

from pydantic import RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import Environment, JournalMode
from app.core.upstream import UpstreamConfig

#: 쓸 수 있는 async 드라이버 (§1.6). 방언 교체는 `DATABASE_URL` 한 줄이지만,
#: **아무 URL 이나 받아주지는 않는다** — 여기 없는 드라이버는 기동 시점에 거부된다.
SUPPORTED_DRIVERS: Final = frozenset(
    {
        'sqlite+aiosqlite',
        'postgresql+psycopg',
        'postgresql+asyncpg',
        'mysql+asyncmy',
        'mysql+aiomysql',
        'mariadb+asyncmy',
    }
)

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
    # SQLite 를 쓴다 (§1.6). 방언을 바꾸는 것은 **이 한 줄**이다:
    #   postgresql+psycopg://app:app@localhost:5432/app
    #   mysql+asyncmy://app:app@localhost:3306/app
    # 드라이버 접두사가 붙어야 async 로 돈다.
    database_url: str = 'sqlite+aiosqlite:///./var/app.db'
    db_echo: bool = False

    # --- SQLite 전용 (다른 방언에서는 무시된다)
    #: SQLite 는 기본이 OFF 다. 켜지 않으면 FK 가 장식이 된다.
    db_foreign_keys: bool = True
    #: WAL 이라야 읽기가 쓰기를 막지 않는다. 쓰기는 여전히 한 번에 하나다.
    db_journal_mode: JournalMode = JournalMode.wal
    #: 쓰기 잠금 대기 시간(ms). 0 이면 경합 시 즉시 'database is locked'.
    db_busy_timeout_ms: int = 5000

    # --- 서버 DB 전용 (SQLite 에서는 무시된다 — 파일이라 커넥션 풀이 의미가 없다)
    #: 워커 프로세스마다 이만큼 잡는다. 프로세스 수를 곱한 값이 서버의 최대 연결 수를 넘으면 안 된다.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    #: 죽은 연결을 미리 걸러낸다. 방화벽·프록시가 유휴 연결을 끊는 환경에서 필수다.
    db_pool_pre_ping: bool = True
    #: 이 시간이 지난 연결은 버리고 새로 맺는다. MySQL 의 wait_timeout(기본 8시간)보다 짧아야 한다.
    db_pool_recycle_seconds: int = 3600

    # --- redis
    redis_url: RedisDsn = 'redis://localhost:6379/0'  # type: ignore[assignment]

    # --- security (§2.2: 토큰 encode/decode 만. 도메인은 모른다)
    jwt_secret: SecretStr = SecretStr(INSECURE_JWT_SECRET)
    jwt_algorithm: str = 'HS256'
    jwt_issuer: str = 'my-fastapi'
    access_token_ttl_seconds: int = 60 * 15
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14

    # --- 첨부파일 (§4.9). 저장소 인스턴스는 lifespan 이 만든다 (§2.1)
    #: 로컬 저장소의 루트. S3 로 바꾸면 이 값 대신 버킷 설정이 온다.
    storage_root: str = './var/uploads'
    #: 업로드 상한. 저장하면서 재고, 넘으면 그 자리에서 끊는다 — 다 받은 뒤에 재면
    #: 상한이 디스크를 지켜주지 못한다.
    attachment_max_bytes: int = 10 * 1024 * 1024
    #: 확장자 허용 목록. 클라이언트가 보낸 `Content-Type` 은 판정에 쓰지 않는다.
    attachment_allowed_extensions: tuple[str, ...] = (
        'jpg',
        'jpeg',
        'png',
        'gif',
        'webp',
        'pdf',
        'txt',
        'csv',
        'zip',
        'docx',
        'xlsx',
        'pptx',
    )

    # --- 백그라운드 작업 (§4.4, §4.5, §4.9). 0 이면 그 작업을 돌리지 않는다
    #: 조회수 버퍼를 DB 에 반영하는 주기. 짧을수록 조회수가 최신이고 쓰기가 잦다.
    view_flush_interval_seconds: int = 30
    #: `comment_count` 드리프트 보정 주기. 야간 배치를 전제로 한 값이다.
    comment_count_reconcile_interval_seconds: int = 60 * 60 * 24
    #: 고아 첨부 정리 주기.
    attachment_cleanup_interval_seconds: int = 60 * 60
    #: 이 시간이 지나도 글에 붙지 않은 첨부는 고아로 본다. 방금 올라온 것은 진행 중이다.
    attachment_orphan_ttl_seconds: int = 60 * 60 * 24

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
    def dialect(self) -> str:
        """`sqlite+aiosqlite://...` → `sqlite`. 방언을 묻는 곳은 전부 이걸 쓴다."""
        return self.database_url.split('+', 1)[0].split('://', 1)[0]

    @property
    def is_sqlite(self) -> bool:
        return self.dialect == 'sqlite'

    @property
    def redis_dsn(self) -> str:
        return str(self.redis_url)

    @field_validator('database_url')
    @classmethod
    def _require_supported_async_driver(cls, value: str) -> str:
        """동기 드라이버를 넣으면 기동은 되고 첫 쿼리에서 죽는다. 여기서 막는다.

        방언 목록을 여기 두는 이유: 지원한다고 말한 방언과 실제로 테스트한 방언이
        갈라지는 것을 막기 위해서다. 새 방언을 지원하려면 **여기에 추가하고**
        `common/db/engine.py` 를 손보고 테스트를 그 방언으로 한 번 돌려야 한다 (§1.6).
        """
        scheme = value.split('://', 1)[0]
        if '+' not in scheme:
            raise ValueError(f'async 드라이버를 명시해라 (예: sqlite+aiosqlite://). 받은 값: {value!r}')
        if scheme not in SUPPORTED_DRIVERS:
            supported = ', '.join(sorted(SUPPORTED_DRIVERS))
            raise ValueError(f'지원하지 않는 드라이버다. 쓸 수 있는 것: {supported}. 받은 값: {value!r}')
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
