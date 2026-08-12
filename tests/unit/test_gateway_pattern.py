"""gateway 패턴의 실행 가능한 명세 (§5).

**여기 있는 `WeatherGateway` 가 새 업스트림을 붙일 때 베낄 템플릿이다.** 문서의 예시가
아니라 실제로 도는 코드라, 기반 코드가 바뀌면 이 테스트가 먼저 깨진다.

검증하는 것은 세 가지:
1. wire DTO(상대의 모양) → 도메인 타입(우리 어휘) 변환이 gateway 안에서 끝난다
2. 상태코드에 의미를 주는 것은 gateway 다 (전송 계층은 404가 무슨 뜻인지 모른다)
3. 상대가 응답 형태를 바꾸면 `upstream.bad_payload` 로 구분된다
"""

from dataclasses import dataclass

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.common.errors import NotFoundError
from app.common.errors.exceptions import UpstreamPayloadError, UpstreamStatusError, UpstreamTimeoutError
from app.common.http.client import UpstreamClient
from app.common.http.gateway import Gateway
from app.common.http.registry import UpstreamRegistry
from app.core.upstream import UpstreamConfig

# ------------------------------------------------------- 템플릿: wire DTO (비공개)


class _WeatherPayload(BaseModel):
    """**A 서버가 보내는 모양.** 이 클래스는 gateway 밖으로 나가지 않는다.

    - `extra='ignore'` — 상대가 필드를 추가해도 우리가 깨지지 않는다
    - `alias` — 상대의 이름을 우리 이름으로 바꾸는 지점이 여기 한 곳이다
    """

    model_config = ConfigDict(extra='ignore', populate_by_name=True)

    city_name: str = Field(alias='cityName')
    temp_c: float = Field(alias='temperatureCelsius')


# ------------------------------------------------------- 템플릿: 도메인 타입 (공개)


@dataclass(frozen=True, slots=True)
class Weather:
    """**우리 어휘.** service 가 보는 것은 이것뿐이다."""

    city: str
    celsius: float


# ------------------------------------------------------------- 템플릿: gateway


class WeatherGateway(Gateway):
    upstream = 'weather'

    @classmethod
    async def fetch(cls, *, upstreams: UpstreamRegistry, city: str) -> Weather:
        try:
            response = await cls.client(upstreams).request('GET', '/weather', params={'city': city})
        except UpstreamStatusError as exc:
            # 상태코드의 의미는 여기서 정한다. 전송 계층은 이걸 알 수 없다.
            if exc.upstream_status == 404:
                raise NotFoundError(code='user.not_found') from exc
            raise

        payload = cls.parse(response, _WeatherPayload)
        return Weather(city=payload.city_name, celsius=payload.temp_c)


# ------------------------------------------------------------------------ 픽스처


def _registry(handler) -> UpstreamRegistry:
    config = UpstreamConfig(
        base_url='https://weather.example.com',
        max_retries=0,
        retry_backoff_seconds=0.001,
    )
    http_client = httpx.AsyncClient(base_url=str(config.base_url), transport=httpx.MockTransport(handler))
    return UpstreamRegistry({'weather': UpstreamClient('weather', config, http_client)})


# -------------------------------------------------------------------------- 검증


async def test_wire_shape_is_translated_into_our_vocabulary():
    """상대는 camelCase 로 `cityName` 을 준다. 우리 타입에는 `city` 만 있다."""
    registry = _registry(lambda request: httpx.Response(200, json={'cityName': '서울', 'temperatureCelsius': 21.5}))

    weather = await WeatherGateway.fetch(upstreams=registry, city='seoul')

    assert weather == Weather(city='서울', celsius=21.5)


async def test_unknown_upstream_fields_are_ignored():
    """상대가 필드를 추가하는 것은 흔한 일이고, 그걸로 우리가 깨져서는 안 된다."""
    registry = _registry(
        lambda request: httpx.Response(
            200,
            json={'cityName': '서울', 'temperatureCelsius': 21.5, 'humidity': 60, 'nested': {'a': 1}},
        )
    )

    weather = await WeatherGateway.fetch(upstreams=registry, city='seoul')

    assert weather.city == '서울'


async def test_gateway_maps_404_to_a_domain_error():
    """전송 계층은 502를 냈고, gateway 가 도메인 의미를 부여한다."""
    registry = _registry(lambda request: httpx.Response(404))

    with pytest.raises(NotFoundError) as caught:
        await WeatherGateway.fetch(upstreams=registry, city='atlantis')

    assert caught.value.code == 'user.not_found'
    assert caught.value.status_code == 404


async def test_unmapped_statuses_stay_upstream_errors():
    """gateway 가 의미를 주지 않은 것은 502/503 으로 나간다 — 우리 잘못이 아니라고 말한다."""
    registry = _registry(lambda request: httpx.Response(418))

    with pytest.raises(UpstreamStatusError):
        await WeatherGateway.fetch(upstreams=registry, city='seoul')


async def test_a_changed_contract_is_its_own_error_code():
    """상대가 필드명을 바꾸면 우리 버그(500)가 아니라 `upstream.bad_payload` 다.

    대응이 다르기 때문에 구분해야 한다 — 우리 버그는 코드를 고치고, 상대의 변경은
    어댑터를 고치거나 상대에게 연락한다.
    """
    registry = _registry(lambda request: httpx.Response(200, json={'city_name': '서울', 'temp': 21.5}))

    with pytest.raises(UpstreamPayloadError) as caught:
        await WeatherGateway.fetch(upstreams=registry, city='seoul')

    assert caught.value.code == 'upstream.bad_payload'


async def test_transport_failure_surfaces_as_a_timeout():
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout('slow')

    registry = _registry(_timeout)

    with pytest.raises(UpstreamTimeoutError):
        await WeatherGateway.fetch(upstreams=registry, city='seoul')


async def test_an_unconfigured_upstream_fails_loudly():
    """설정 누락은 설정 오류로 보여야 한다 — 엉뚱한 AttributeError 가 아니라."""
    empty = UpstreamRegistry({})

    with pytest.raises(RuntimeError, match='설정되어 있지 않다'):
        await WeatherGateway.fetch(upstreams=empty, city='seoul')
