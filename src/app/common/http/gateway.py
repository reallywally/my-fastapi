"""업스트림 어댑터의 기반 (§5).

## 세 종류의 데이터를 섞지 않는다

| | 무엇 | 누가 소유 | 바뀌면 |
|---|---|---|---|
| `model.py` | SQLAlchemy 행 | **우리** | 마이그레이션을 쓴다 (§2.3) |
| `schema.py` | 우리 API 계약 | **우리** | 화면이 깨진다 (§0) |
| `gateway.py` 의 wire DTO | 남의 API 응답 | **상대** | 예고 없이 바뀐다 |

세 번째가 문제다. 상대의 응답을 그대로 쓰면:

- **응답으로 흘리면** 우리 API 가 상대의 필드명·구조에 묶인다. 상대가 `cityName` 을
  `city_name` 으로 바꾸면 우리 클라이언트가 깨진다. 우리가 통제하지 못하는 계약이 된다.
- **DB에 그대로 저장하면** 상대의 스키마가 우리 테이블 스키마가 된다.

**그래서 규칙: wire DTO 는 `gateway.py` 밖으로 나가지 않는다.** gateway 의 반환 타입은
모듈이 정의한 타입(frozen dataclass 또는 model)이다. 상대가 응답을 바꾸면 고칠 파일이
정확히 하나다.

## 템플릿

```python
class _WeatherPayload(BaseModel):                  # wire DTO — 밑줄로 시작한다
    model_config = ConfigDict(extra='ignore')      # 상대가 필드를 더해도 깨지지 않는다
    city_name: str = Field(alias='cityName')       # 상대의 이름 → 우리 이름
    temp_c: float = Field(alias='temperatureCelsius')

@dataclass(frozen=True, slots=True)
class Weather:                                     # 우리 어휘. 이것만 밖으로 나간다
    city: str
    celsius: float

class WeatherGateway(Gateway):
    upstream = 'weather'                           # 설정의 키

    @classmethod
    async def fetch(cls, *, upstreams: UpstreamRegistry, city: str) -> Weather:
        try:
            response = await cls.client(upstreams).request('GET', '/weather', params={'city': city})
        except UpstreamStatusError as exc:
            if exc.upstream_status == 404:
                raise NotFoundError(code='weather.city_unknown') from exc
            raise                                  # 나머지는 502/503 으로 나간다
        payload = cls.parse(response, _WeatherPayload)
        return Weather(city=payload.city_name, celsius=payload.temp_c)
```

상태코드에 의미를 주는 것은 여기다. 전송 계층(`client.py`)은 404가 무슨 뜻인지 모른다.
"""

import logging
from typing import ClassVar

import httpx
from pydantic import BaseModel, ValidationError

from app.common.errors.exceptions import UpstreamPayloadError
from app.common.http.client import UpstreamClient
from app.common.http.registry import UpstreamRegistry

logger = logging.getLogger(__name__)


class Gateway:
    """업스트림 하나에 대응하는 어댑터.

    서비스처럼 stateless 다 (§1.3) — `upstreams` 를 인자로 받는다. `db` / `redis` 를
    받는 것과 같은 방식이다 (§2.1).
    """

    #: 설정(`UPSTREAMS`) 의 키. 서브클래스가 반드시 정한다.
    upstream: ClassVar[str]

    @classmethod
    def client(cls, upstreams: UpstreamRegistry) -> UpstreamClient:
        return upstreams.get(cls.upstream)

    @classmethod
    def parse[T: BaseModel](cls, response: httpx.Response, model: type[T]) -> T:
        """wire DTO 로 검증한다. 실패는 '상대가 계약을 바꿨다' 는 신호다.

        검증 오류를 그대로 500으로 흘리지 않고 `upstream.bad_payload` 로 구분하는 이유:
        우리 버그와 상대의 변경은 대응이 다르다. 전자는 코드를 고치고, 후자는 상대에게
        연락하거나 어댑터를 고친다. 로그에서 구분되어야 한다.
        """
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning(
                'upstream %s payload did not match %s: %s',
                cls.upstream,
                model.__name__,
                exc.errors(include_url=False),
            )
            raise UpstreamPayloadError(upstream=cls.upstream, upstream_status=response.status_code) from exc
