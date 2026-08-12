"""업스트림 설정 (§5). **n개로 늘어나도 코드가 늘지 않는지** 가 핵심이다."""

import pytest
from pydantic import ValidationError

from app.common.http.registry import create_registry
from app.core.config import Settings
from app.core.constants import Environment
from app.core.upstream import UpstreamConfig


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_no_upstreams_by_default(clean_env):
    assert Settings().upstreams == {}


def test_many_upstreams_from_one_json_variable(clean_env, monkeypatch: pytest.MonkeyPatch):
    """서버를 추가하는 것은 설정 한 줄이다. A, B, C... 코드 변경 없음."""
    monkeypatch.setenv(
        'UPSTREAMS',
        '{"a":{"base_url":"https://a.example.com"},'
        '"b":{"base_url":"https://b.example.com","read_timeout_seconds":10},'
        '"c":{"base_url":"https://c.example.com","max_retries":0}}',
    )

    upstreams = Settings().upstreams

    assert sorted(upstreams) == ['a', 'b', 'c']
    assert upstreams['b'].read_timeout_seconds == 10
    assert upstreams['c'].max_retries == 0
    assert upstreams['a'].read_timeout_seconds == 5.0  # 기본값


def test_a_single_key_can_be_overridden_without_rewriting_the_json(clean_env, monkeypatch: pytest.MonkeyPatch):
    """k8s 에서 하나만 바꿀 때 JSON 전체를 다시 쓰지 않아도 된다."""
    monkeypatch.setenv('UPSTREAMS', '{"a":{"base_url":"https://a.example.com"}}')
    monkeypatch.setenv('UPSTREAMS__A__READ_TIMEOUT_SECONDS', '30')

    assert Settings().upstreams['a'].read_timeout_seconds == 30


def test_base_url_is_required():
    with pytest.raises(ValidationError):
        UpstreamConfig()  # type: ignore[call-arg]


def test_unknown_keys_are_rejected():
    """오타 난 설정 키가 조용히 무시되면 타임아웃이 안 걸린 채로 배포된다."""
    with pytest.raises(ValidationError):
        UpstreamConfig(base_url='https://a.example.com', read_timout_seconds=1)  # type: ignore[call-arg]


def test_timeouts_must_be_positive():
    with pytest.raises(ValidationError):
        UpstreamConfig(base_url='https://a.example.com', read_timeout_seconds=0)


def test_production_refuses_to_disable_tls_verification(clean_env):
    with pytest.raises(ValidationError, match='TLS'):
        Settings(
            environment=Environment.production,
            jwt_secret='a-real-production-secret-32-bytes-long',
            upstreams={'a': UpstreamConfig(base_url='https://a.example.com', verify_tls=False)},
        )


def test_local_allows_disabling_tls_verification(clean_env):
    """사내 자체서명 인증서를 쓰는 개발 환경이 있다. 운영에서만 막는다."""
    settings = Settings(
        environment=Environment.local,
        upstreams={'a': UpstreamConfig(base_url='https://a.example.com', verify_tls=False)},
    )

    assert settings.upstreams['a'].verify_tls is False


async def test_registry_exposes_every_configured_name():
    registry, raw = create_registry(
        {
            'a': UpstreamConfig(base_url='https://a.example.com'),
            'b': UpstreamConfig(base_url='https://b.example.com'),
        }
    )
    try:
        assert registry.names() == ('a', 'b')
        assert 'a' in registry
        assert 'zzz' not in registry
        assert registry.get('a').name == 'a'
    finally:
        for client in raw:
            await client.aclose()


async def test_registry_names_the_configured_upstreams_in_the_error():
    """설정 이름을 틀렸을 때 무엇이 있는지 알려줘야 고칠 수 있다."""
    registry, raw = create_registry({'a': UpstreamConfig(base_url='https://a.example.com')})
    try:
        with pytest.raises(RuntimeError, match='설정된 이름: a'):
            registry.get('typo')
    finally:
        for client in raw:
            await client.aclose()


async def test_timeouts_are_all_set_on_the_built_client():
    """§5 — 4종 전부 명시한다. pool 이 None 이면 느린 업스트림 앞에 요청이 무한정 쌓인다."""
    _, raw = create_registry({'a': UpstreamConfig(base_url='https://a.example.com', pool_timeout_seconds=1.5)})
    try:
        timeout = raw[0].timeout
        assert timeout.connect is not None
        assert timeout.read is not None
        assert timeout.write is not None
        assert timeout.pool == 1.5
    finally:
        for client in raw:
            await client.aclose()
