"""외부 서버(업스트림) 설정. 값만 들고 있다 — 연결은 lifespan 이 만든다 (§2.1).

**업스트림은 n개로 늘어난다.** 그래서 서버마다 클래스를 만들지 않고 `이름 → 설정` 맵으로
둔다. 새 서버를 붙이는 것은 설정 한 줄이고 코드 변경이 아니다 (§3.1 과 같은 원칙:
확장은 정적으로, 런타임 설치가 아니라 배포로).
"""

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr


class UpstreamConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    base_url: HttpUrl

    # --- 타임아웃: 전부 명시한다.
    # 타임아웃 없는 외부 호출은 상대 서버가 느려질 때 **우리 서버를 같이 죽인다.**
    # 워커가 응답을 기다리며 점유되고, 그 사이 우리 쪽 요청이 큐에 쌓인다.
    connect_timeout_seconds: float = Field(default=2.0, gt=0)
    read_timeout_seconds: float = Field(default=5.0, gt=0)
    write_timeout_seconds: float = Field(default=5.0, gt=0)
    #: 커넥션 풀 대기 상한. 이게 없으면 느린 업스트림 앞에 요청이 무한정 줄을 선다.
    pool_timeout_seconds: float = Field(default=2.0, gt=0)

    #: 업스트림 하나가 우리 커넥션을 다 먹지 않게 격리한다 (bulkhead).
    max_connections: int = Field(default=20, ge=1)
    max_keepalive_connections: int = Field(default=10, ge=0)

    #: 멱등한 메서드에만 적용된다 (`common/http/client.py` 참조).
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.2, gt=0)
    retry_backoff_max_seconds: float = Field(default=2.0, gt=0)

    api_key: SecretStr | None = None
    api_key_header: str = 'Authorization'

    #: 운영에서 끄면 기동이 거부된다 (`core/config.py` 의 production 검사).
    verify_tls: bool = True

    #: 주면 `/health/ready` 가 이 경로를 찔러본다. 없으면 검사하지 않는다.
    health_path: str | None = None
