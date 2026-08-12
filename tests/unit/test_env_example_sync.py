"""Phase 1 체크리스트 — `.env.example` 동기화.

설정 필드를 추가하고 `.env.example` 을 잊는 것은 배포 시점에야 드러난다.
여기서 잡는다.
"""

from app.core.config import Settings
from app.core.paths import PROJECT_ROOT

ENV_EXAMPLE = PROJECT_ROOT / '.env.example'


def _documented_keys() -> set[str]:
    keys = set()
    for raw in ENV_EXAMPLE.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        keys.add(line.split('=', 1)[0].strip())
    return keys


def test_every_setting_is_documented():
    expected = {name.upper() for name in Settings.model_fields}
    missing = expected - _documented_keys()

    assert not missing, f'.env.example 에 빠진 설정: {sorted(missing)}'


def test_no_stale_keys_in_env_example():
    expected = {name.upper() for name in Settings.model_fields}
    stale = _documented_keys() - expected

    assert not stale, f'.env.example 에 더 이상 없는 설정이 남아 있다: {sorted(stale)}'
