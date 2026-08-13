# my-fastapi

게시판 API 서버. 설계 기준은 [ARCHITECTURE.md](./ARCHITECTURE.md) — 코드보다 그쪽이 먼저다.

현재 상태: **Phase 1~3 완료 + Phase 4 진행 중** (뼈대 + 공용 계층 + `user` 모듈,
게시판 컨텍스트의 `board`·`post`·`comment`). 다음은 조회수 버퍼 (§4.5).

DB는 **SQLite**다 (§1.6). 띄울 서버가 없고 `var/app.db` 파일 하나가 전부다.
**ORM은 쓰지 않는다** — SQLAlchemy Core만 쓰고, 행은 dataclass로 받는다. Core를 남긴
이유는 하나다: 나중에 PostgreSQL·MySQL로 옮길 때 방언 차이를 대신 흡수해줄 계층이
필요해서다. 방언 교체는 `DATABASE_URL` 한 줄이고, 그게 사실인지는
`tests/unit/test_dialect_portability.py`가 매번 검사한다.

`modules/user/` 가 이후 모든 모듈의 템플릿이다 — 새 모듈은 그 5파일 구성을 따른다.
**쓰기 엔드포인트는 Phase 5까지 전부 401** 이다 (`modules/user/deps.py`). 가짜 주체를
넣어두면 인가가 걸린 척하는 엔드포인트가 되기 때문에, 안전한 쪽으로 틀리게 뒀다.
읽기는 `read_role='anonymous'` 인 게시판에 한해 열려 있다 (§4.6).

## 시작하기

```bash
uv sync --all-groups            # 의존성
cp .env.example .env            # 설정
docker compose up -d            # redis (DB 는 파일이라 여기 없다)
uv run alembic upgrade head     # 스키마 (앱이 만들지 않는다 — §2.3)
uv run uvicorn app.main:app --reload
```

- API 문서: http://localhost:8000/docs
- liveness: `GET /health` / readiness: `GET /health/ready`

## 개발 명령

```bash
uv run pytest                   # 전체. Docker 없으면 통합/E2E 는 skip
uv run pytest tests/unit        # 유닛만. DB·Redis 불필요, 밀리초 단위
uv run ruff check . && uv run ruff format .
uv run lint-imports             # 의존 방향 검사 (§2.2)
uv run pre-commit install       # 커밋 훅
```

**테스트에 Docker 가 필요 없다.** DB 는 임시 SQLite 파일이고 Redis 는 `fakeredis` 다.
진짜 Redis 로 검증하고 싶으면:

```bash
TEST_REDIS_URL=redis://localhost:6379/15 uv run pytest
```

마이그레이션 추가:

```bash
uv run alembic revision --autogenerate -m "설명"
uv run alembic check            # 모델과 마이그레이션이 어긋나면 실패
```

> 새 모델은 `src/app/bootstrap/models.py` 에 import 를 추가해야 한다. 빼먹으면
> autogenerate 가 에러 없이 **빈 리비전**을 만들고 `alembic check` 도 통과한다.
> `tests/unit/test_model_registry.py` 가 잡는다.

## DB 방언 바꾸기 (§1.6)

`DATABASE_URL` 한 줄이다. 지원 목록은 `core/config.py` 의 `SUPPORTED_DRIVERS` 에 있고,
목록에 없는 URL 은 기동 시점에 거부된다.

```bash
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/app
DATABASE_URL=mysql+asyncmy://app:app@localhost:3306/app
```

바꾸기 전에 알아야 할 것:

1. **드라이버를 설치해야 한다** (`uv add psycopg` 또는 `uv add asyncmy`).
2. **마이그레이션을 그 DB 에 처음부터 다시 올린다.** 리비전은 방언 중립으로 쓰여 있어
   그대로 돌지만, SQLite 파일의 데이터는 따라가지 않는다.
3. **테스트를 그 방언으로 한 번 돌린다.** `tests/unit/test_dialect_portability.py` 는
   컴파일만 검사한다 — 잠금·격리 수준·정렬 같은 런타임 차이는 실제로 돌려야 안다.
4. **§4.8 전문검색은 다시 짜야 한다.** SQLite FTS5 / PostgreSQL `TSVECTOR` /
   MySQL FULLTEXT 는 어떤 추상화로도 안 덮인다.

## 외부 서버 호출 (§5)

서버는 이름 → 설정 맵으로 둔다. **추가는 설정 한 줄이고 코드 변경이 아니다.**

```bash
UPSTREAMS='{"a":{"base_url":"https://a.example.com","health_path":"/healthz"},
            "b":{"base_url":"https://b.example.com","read_timeout_seconds":10}}'
```

호출은 `gateway.py` 에서 한다. **상대의 응답 DTO는 그 파일 밖으로 나가지 않는다** —
나가면 상대의 필드명이 우리 API 계약이나 테이블 스키마가 된다 (§5.5).

```python
class _WeatherPayload(BaseModel):                # 상대의 모양. 밑줄로 시작한다
    model_config = ConfigDict(extra='ignore')
    city_name: str = Field(alias='cityName')

@dataclass(frozen=True, slots=True)
class Weather:                                   # 우리 어휘. 이것만 밖으로 나간다
    city: str

class WeatherGateway(Gateway):
    upstream = 'a'                               # UPSTREAMS 의 키

    @classmethod
    async def fetch(cls, *, upstreams, city: str) -> Weather:
        response = await cls.client(upstreams).request('GET', '/weather', params={'city': city})
        return Weather(city=cls.parse(response, _WeatherPayload).city_name)
```

동작하는 템플릿은 `tests/unit/test_gateway_pattern.py` 에 있다 — 문서가 아니라 도는 코드다.

알아둘 것:
- **POST/PATCH 는 재시도하지 않는다.** 타임아웃은 "처리 안 됐다" 가 아니라 "결과를 못
  봤다" 다. 필요하면 `idempotent=True` 로 명시한다
- 상대의 상태코드를 우리 응답으로 흘리지 않는다. `UpstreamStatusError` 를 잡아
  도메인 에러로 바꾸는 것이 gateway 의 일이다
- 업스트림이 죽어도 `/health/ready` 는 200 이다. 보고만 하고 판정에 넣지 않는다 (§5.7)

## 새 모듈 추가하기

`modules/user/` 를 그대로 베낀다 (§6 Phase 3 에서 확정된 템플릿):

```
modules/<name>/
├─ __init__.py   router 만 노출
├─ router.py     HTTP 만. 읽기 ConnDep / 쓰기 TxDep
├─ schema.py     요청·응답. 응답은 허용 목록으로 (모델 직렬화 금지)
├─ service.py    규칙. commit·Request 금지, 에러는 코드로
├─ repository.py 쿼리만
└─ model.py      Table 정의 + 행 dataclass
```

그리고 세 곳에 등록한다: `bootstrap/router.py`, `bootstrap/models.py`,
`locale/{ko,en}.json`(에러 코드). 뒤의 둘은 잊어도 테스트가 잡는다.

## 구조

```
src/app/
  bootstrap/   조립만. 아래를 전부 import 가능 (composition root)
  modules/     기능. common, core import 가능        ← Phase 3~5 에서 채워진다
  common/      공용 인프라. core만 import 가능. 도메인을 모른다
  core/        설정·상수·경로. 아무것도 import 하지 않는다
```

의존 방향은 `lint-imports` 가 CI 에서 막는다. §7 규칙표의 나머지 항목은
`tests/unit/test_architecture_rules.py` 가 AST 로 검사한다.

## 알아둘 것

- **I/O 자원은 `bootstrap/lifespan.py` 에서만 만든다.** `import` 만으로는 아무 연결도
  열리지 않는다 (§2.1). `tests/unit/test_import_purity.py` 가 이걸 지킨다.
- **엔진은 `common/db/engine.py` 로만 만든다.** SQLite 는 PRAGMA 를 안 걸면 외래키가
  꺼진 채로 돈다 (§1.6). `create_async_engine` 을 직접 부르면 테스트가 막는다.
- **트랜잭션은 엔드포인트가 결정한다.** 읽기는 `ConnDep`, 쓰기는 `TxDep` (§1.1).
  service/repository 는 `commit()` 하지 않는다.
- **soft delete 조건을 손으로 쓰지 않는다.** `select_alive()` 가 붙인다 (§2.4).
  삭제분까지 보려면 `select_rows()` 를 쓴다 — 조건이 한 곳에만 있다는 것이 요점이다.
- **방언 전용 코드는 `common/db/engine.py`·`types.py` 안에만 있다** (§1.6).
  `modules/` 에서 `text()` 나 `sqlalchemy.dialects` 를 쓰면 테스트가 막는다.
- **에러는 메시지가 아니라 코드로 raise 한다.** `raise NotFoundError(code='post.not_found')`.
  문구는 `src/app/locale/{ko,en}.json` 이 갖는다 (§2.6).
- **시각은 항상 aware UTC.** naive 를 저장하려 하면 `UTCDateTime` 이 거부한다.
- **테스트 격리는 롤백이다.** truncate 하지 않는다 (§2.8).
- 화면은 이 저장소에 없다. JSON API 서버다 (§0).
