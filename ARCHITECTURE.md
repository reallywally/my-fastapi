# my-fastapi 설계 문서

`fastapi-best-architecture`(이하 FBA) 코드 리뷰 결과를 바탕으로, **가져올 것 / 고칠 것 / 버릴 것**을 정리한 설계 기준서.

FBA는 "잘 만든 실무형 스캐폴딩"이지만 이름만큼의 아키텍처는 아니다. 구조 일관성과 트랜잭션 설계는 배울 만하고, 의존성 방향·자원 라이프사이클·테스트는 다시 짜야 한다.

---

## 1. 가져올 것 (검증된 부분)

### 1.1 트랜잭션 경계를 DI로 처리

FBA에서 가장 잘 된 부분. **service/repository는 절대 `commit()` 하지 않는다.** 트랜잭션은 엔드포인트가 어떤 의존성을 선언했는지로 결정된다.

```python
# common/db/session.py
async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory() as session:
        yield session

async def get_db_tx(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.session_factory.begin() as session:
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_db)]
TxDep = Annotated[AsyncSession, Depends(get_db_tx)]
```

```python
@router.get('/{pk}')
async def get_user(db: SessionDep, pk: int): ...        # 읽기: 트랜잭션 없음

@router.post('')
async def create_user(db: TxDep, obj: CreateUser): ...  # 쓰기: 자동 커밋/롤백
```

**규칙 (린트로 강제):**
- `service/`, `repository/` 안에서 `commit()` 금지. `flush()`만 허용.
- 커밋 실패는 예외로 전파되어 DI가 롤백한다.

> FBA는 이 규칙을 21,000줄 전체에서 지켰다 (`commit()` 호출 0회). 그대로 채택.

### 1.2 계층 네이밍과 역할 분리

| 역할 | FBA | my-fastapi | 책임 |
|---|---|---|---|
| 입출력 | `api` | `router.py` | HTTP만. 검증·직렬화·상태코드 |
| 전송 객체 | `schema` | `schema.py` | Pydantic. 요청/응답 계약 |
| 업무 규칙 | `service` | `service.py` | 트랜잭션 내부 로직, 도메인 규칙 |
| 데이터 접근 | `crud` | `repository.py` | 쿼리만. 규칙 없음 |
| 테이블 | `model` | `model.py` | SQLAlchemy 매핑 |

`crud` → `repository`로 개명. `crud`는 CRUD 5개만 있다는 인상을 주는데 실제로는 모든 쿼리가 여기 산다.

### 1.3 stateless service + 모듈 전역 인스턴스

**DI 컨테이너를 도입하지 않는다.** FBA 방식을 유지한다.

```python
class UserService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> User: ...

user_service = UserService()
```

이유:
- 서비스에 인스턴스 상태가 없다. 주입할 게 없는 객체를 wiring하는 건 보일러플레이트다.
- FastAPI의 `Depends`는 엔드포인트에서 시작하는 트리라 **service → service 호출을 풀 수 없다.** 해결하려면 `dependency-injector` 같은 컨테이너를 추가해야 하는데, 의존성 하나 줄이려고 프레임워크를 얹는 거래다.
- Python은 `unittest.mock.patch`로 모듈 참조를 교체할 수 있다. 전역 싱글턴이 곧 테스트 불가는 아니다.
- tiangolo의 `full-stack-fastapi-template`도 같은 패턴이다. 생태계 주류를 벗어날 이유가 없다.

**`Depends`는 요청 스코프 / 횡단 관심사에만 쓴다:** 인증, 인가, DB 세션, Redis, 레이트리밋, 페이지네이션, 현재 사용자.

### 1.4 soft delete에 삭제 행 id를 저장

FBA의 영리한 부분. `deleted`가 boolean이 아니라 `0` 또는 **자기 행의 id**다.

```python
deleted: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')
# __table_args__ = (UniqueConstraint('username', 'deleted'),)
```

boolean이면 `unique(username)` 때문에 삭제된 아이디를 재사용할 수 없다. id를 넣으면 복합 unique가 성립해서 재등록이 가능하다. **채택.**

### 1.5 인프라 스택

- **ruff** 촘촘하게 (`ANN`으로 타입 힌트 강제, `line-length=120`, `quote-style='single'`)
- **msgspec** 기반 `JSONResponse` — orjson보다 빠르고 Pydantic v2와 궁합이 좋다
- **OpenTelemetry + Prometheus** — 처음부터 붙인다. 나중에 붙이면 계측 지점을 놓친다
- **snowflake PK 옵션** — 분산 환경 대비. 기본은 `BIGSERIAL`, 설정으로 전환
- **`ensure_unique_route_names` / `simplify_operation_ids`** — OpenAPI 클라이언트 생성 품질을 위해 유지

---

## 2. 고칠 것

### 2.1 자원 라이프사이클 — import 부작용 제거 ★최우선

**FBA의 문제:**

```python
# backend/database/db.py 하단 — 모듈 import만으로 실행된다
async_engine = create_database_async_engine(get_database_url())
# 그리고 실패하면 create_database_async_engine 안에서 sys.exit()
```

증상:
- `import app.main` 만 해도 DB 설정이 유효해야 한다 → 유닛테스트가 불가능해진다
- 라이브러리 코드가 프로세스를 죽인다 (`sys.exit()`)
- `redis_client`도 전역이라 테스트에서 교체 지점이 없다 → 실제 Redis가 떠 있어야 한다

**해결: 모든 I/O 자원은 `lifespan`에서 만들고 `app.state`에 둔다.**

```python
# bootstrap/lifespan.py
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    engine = create_async_engine(
        settings.database_url,
        pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=3600,
    )
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)

    await app.state.redis.ping()   # 실패하면 예외 → 기동 실패. sys.exit() 쓰지 않는다

    try:
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()
```

```python
# common/db/deps.py
async def get_redis(request: Request) -> Redis:
    return request.app.state.redis

RedisDep = Annotated[Redis, Depends(get_redis)]
```

**service는 `db`를 받듯 `redis`도 인자로 받는다.** stateless 유지하면서 자원만 주입된다.

```python
class UserService:
    @staticmethod
    async def update(*, db: AsyncSession, redis: Redis, pk: int, obj: UpdateUser) -> int: ...
```

얻는 것:
- `import`만으로는 아무 연결도 열리지 않는다 → 순수 유닛테스트 가능
- 테스트에서 `app.state`를 fake로 바꾸면 Redis 없이 돌아간다
- 엔진 정리(`dispose`)가 보장된다

### 2.2 의존성 방향 — 단방향 강제 ★최우선

**FBA의 문제:** 인프라 계층이 도메인 계층을 import한다.

```
common/security/jwt.py:15             → backend.app.admin.model
common/security/jwt.py:200            → app.admin.crud   (함수 내부 import = 순환참조 회피)
middleware/opera_log_middleware.py:13 → app.admin.service
app/admin/crud/crud_user.py:31        → backend.plugin   (역방향)
```

함수 본문 안 `import`가 15곳. 전부 순환 참조를 못 끊어서 런타임으로 미룬 자국이다.

**해결 1 — 계층을 4단으로 못박는다.**

```
bootstrap/   조립만. 아래 전부 import 가능 (composition root)
   ↓
modules/     기능. common, core import 가능
   ↓
common/      공용 인프라. core만 import 가능. 도메인을 절대 모른다
   ↓
core/        설정·상수·경로. 아무것도 import 하지 않는다
```

**해결 2 — 도메인을 아는 코드를 아래층에서 걷어낸다.**

| FBA | my-fastapi |
|---|---|
| `common/security/jwt.py`가 `User` 모델을 import | `common/security/token.py`는 **encode/decode만**. 도메인 import 0.<br>"토큰 → 사용자 로드"는 `modules/auth/deps.py`로 올린다 |
| 미들웨어가 `opera_log_service`를 직접 호출 | 미들웨어는 **큐에 이벤트만 넣는다.** 소비자는 `modules/audit/`에 둔다. `common`은 큐 인터페이스만 안다 |
| `crud_user`가 `plugin.oauth2`를 import | 소셜 계정 조회는 `modules/oauth/`가 담당. user 모듈은 oauth를 모른다 |

**해결 3 — CI에서 기계로 막는다.** 규칙은 문서가 아니라 린트로 존재해야 한다.

```ini
# .importlinter
[importlinter]
root_package = app

[importlinter:contract:layers]
name = Layered architecture
type = layers
layers =
    app.bootstrap
    app.modules
    app.common
    app.core
```

```yaml
# .github/workflows/lint.yml
- run: uv run lint-imports
```

### 2.3 마이그레이션을 유일한 스키마 소스로 ★최우선

**FBA의 문제:** `core/registrar.py:52`에서 기동할 때마다 `await create_tables()`(= `create_all`)를 무조건 실행한다. 그런데 `alembic/versions/`는 **비어 있다**.

`create_all`은 없는 테이블만 만든다. **컬럼 추가·변경·삭제를 반영하지 못한다.** 운영에서 스키마 드리프트가 조용히 쌓인다.

**해결:**
1. 앱 코드에서 `create_all` **완전 제거.** 기동은 스키마를 만들지 않는다.
2. 첫 커밋에 초기 리비전을 포함한다.
3. 마이그레이션은 **배포 단계**에서 실행한다. 앱 프로세스가 아니라.
   ```yaml
   # compose.yaml
   migrate:
     command: alembic upgrade head
   api:
     depends_on: { migrate: { condition: service_completed_successfully } }
   ```
4. CI에서 드리프트를 검사한다.
   ```bash
   alembic check   # 모델과 마이그레이션이 어긋나면 실패
   ```

### 2.4 soft delete 자동화 — 106회 반복 제거

**FBA의 문제:** `deleted=0` 조건을 쿼리마다 손으로 붙인다. **106곳**에 하드코딩되어 있고, **14곳은 빠져 있다.** 하나 놓치면 삭제된 데이터가 노출된다.

**해결: ORM 이벤트로 전역 적용.**

```python
# common/db/soft_delete.py
from sqlalchemy import event, orm
from sqlalchemy.orm import Session

@event.listens_for(Session, 'do_orm_execute')
def _apply_soft_delete_filter(state: orm.ORMExecuteState) -> None:
    if (
        not state.is_select
        or state.is_column_load
        or state.is_relationship_load
        or state.execution_options.get('include_deleted', False)
    ):
        return
    state.statement = state.statement.options(
        orm.with_loader_criteria(SoftDeleteMixin, lambda cls: cls.deleted == 0, include_aliases=True)
    )
```

- `SoftDeleteMixin`을 상속한 모든 모델에 자동 적용된다. 관계 로딩까지 포함.
- 삭제분까지 봐야 하면 명시적으로 opt-out: `select(User).execution_options(include_deleted=True)`
- `AsyncSession`도 내부적으로 sync `Session`을 쓰므로 그대로 동작한다.

### 2.5 캐시 무효화를 한 곳으로

**FBA의 문제:** 사용자 정보가 바뀔 때마다 서비스가 Redis 키 3~5개를 개별로 지운다. 같은 코드가 `user_service` 전반에 흩어져 있다.

```python
await redis.delete_by_prefix(f'{settings.TOKEN_REDIS_PREFIX}:{user_id}')
await redis.delete_by_prefix(f'{settings.TOKEN_REFRESH_REDIS_PREFIX}:{user_id}')
await redis.delete_by_prefix(f'{settings.JWT_USER_REDIS_PREFIX}:{user_id}')
```

하나만 빠뜨려도 권한이 stale해진다 — 잠긴 계정이 계속 요청을 통과한다.

**해결: 세션 저장소 객체가 키 구조를 독점한다.**

```python
# modules/auth/session_store.py
class UserSessionStore:
    async def invalidate(self, redis: Redis, user_id: int, *, keep: str | None = None) -> None:
        """해당 사용자의 토큰·리프레시·캐시된 프로필을 모두 무효화한다."""

    async def invalidate_profile(self, redis: Redis, user_id: int) -> None:
        """프로필 캐시만. 로그인 상태는 유지."""

user_sessions = UserSessionStore()
```

서비스는 한 줄이 된다: `await user_sessions.invalidate(redis, user_id)`
키 접두사는 이 파일 밖으로 나가지 않는다.

### 2.6 에러 메시지를 코드로

**FBA의 문제:** 에러 메시지가 중국어 하드코딩이다 (`raise errors.NotFoundError(msg='用户不存在')`). i18n 모듈이 있는데 정작 비즈니스 에러에는 적용되지 않는다. 299개 파일 중 213개가 중국어다.

**해결:**

```python
raise NotFoundError(code='user.not_found')          # 메시지 아님, 코드
```

- 메시지는 `locale/{ko,en}.json` 카탈로그에서 해석한다.
- 예외 핸들러가 `Accept-Language`를 보고 렌더링한다.
- **코드·주석·문서는 영어 또는 한국어로 통일.** 섞지 않는다.

### 2.7 API 레이어 누수 차단

**FBA의 문제:** 서비스 3곳이 `Request` 객체를 직접 받는다. HTTP 관심사가 도메인으로 스며든다.

**해결:** 서비스 시그니처에 `Request`, `Response`, `UploadFile` 금지. 필요한 값은 라우터가 꺼내서 원시 타입으로 넘긴다.

```python
# 나쁨
async def update_permission(*, db, request: Request, pk: int): ...

# 좋음
async def update_permission(*, db, actor_id: int, pk: int): ...
```

파일 업로드는 라우터가 `UploadFile`을 받아 저장한 뒤 서비스에는 경로/바이트만 전달한다.

> FBA에서 이 누수가 실제 버그를 만들었다. `user_service.py:172,177`이 `request.user.id`와 비교해야 할 것을 `user.id`와 비교한다. `user`는 `pk`로 조회한 값이라 `user.id == pk`가 항상 참 → 조건이 상수가 되고 한쪽 분기가 죽은 코드다. 관리자가 타인의 `multi_login`을 끄면 **대상자가 아니라 관리자 본인의 값**을 뒤집어 저장한다.

### 2.8 테스트를 1급 시민으로 ★최우선

**FBA의 문제:** 21,000줄 / 299 파일에 테스트 **1개, 7줄**(`test_logout`). `mock`/`patch` 사용 흔적 0건. 리팩터링 안전망이 없다.

**해결: 첫 커밋부터 테스트 인프라를 깐다.**

```
tests/
├─ conftest.py          # testcontainers로 Postgres/Redis 기동
├─ factories.py         # 테스트 데이터 팩토리
├─ unit/                # 순수 로직. DB·Redis 없음. 밀리초 단위
├─ integration/         # repository ↔ 실제 DB
└─ e2e/                 # httpx AsyncClient로 라우터 관통
```

```python
# conftest.py 핵심
@pytest.fixture(scope='session')
async def app_with_test_resources():
    async with PostgresContainer('postgres:16') as pg, RedisContainer() as rd:
        app = create_app()
        app.state.engine = create_async_engine(pg.get_connection_url())
        app.state.session_factory = async_sessionmaker(app.state.engine, expire_on_commit=False)
        app.state.redis = Redis.from_url(rd.get_connection_url())
        await run_migrations(app.state.engine)   # create_all 아님. alembic으로
        yield app
```

- 각 테스트는 **트랜잭션 안에서 돌고 롤백**한다. 테스트 간 격리를 DB truncate로 하지 않는다.
- `pytest-asyncio` + `httpx.AsyncClient`. FBA처럼 sync `TestClient`를 쓰면 async 경로를 제대로 못 탄다.
- **CI 커버리지 게이트를 건다.** 숫자는 낮게 시작해도 되지만 *내려가는 것*은 막는다.

```toml
[tool.coverage.report]
fail_under = 70
```

### 2.9 파일 크기 상한

FBA의 `cli.py`는 1,020줄이다. `C901`(복잡도)에 더해 파일 길이도 관리한다. 400줄을 넘으면 쪼갠다.

---

## 3. 버릴 것

### 3.1 런타임 플러그인 설치

**FBA의 동작:** `POST /plugins`가
1. 임의의 git URL을 `clone` 하고
2. `subprocess.check_call(pip_install)`로 의존성을 설치하고
3. 플러그인의 `.env.example`을 운영 `.env`에 **append** 한다

슈퍼유저 권한이 걸려 있지만 사실상 원격 코드 실행 경로다. 게다가:
- **컨테이너 불변성이 깨진다.** 재배포하면 설치한 플러그인이 사라진다
- **빌드가 재현 불가능해진다.** 같은 이미지가 인스턴스마다 다른 패키지를 갖는다
- **공급망 위험**을 런타임으로 옮긴다

**대안 — 정적 확장:**

```toml
[project.optional-dependencies]
oauth = ["authlib>=1.3"]
notice = ["aiosmtplib>=3.0"]
```

```python
# bootstrap/modules.py — 설정으로 켜고 끈다. 코드는 이미지에 있다
ENABLED_MODULES = settings.enabled_modules   # ['user', 'auth', 'audit']
```

확장이 필요하면 이미지를 다시 빌드한다. 그게 정상이다.

### 3.2 기동 시 `create_all()`

2.3 참조. 마이그레이션이 유일한 스키마 소스다.

### 3.3 `sys.exit()`로 프로세스 종료

라이브러리·모듈 코드는 예외만 올린다. 종료 결정은 `main`과 프로세스 관리자(k8s, systemd)의 몫이다.

### 3.4 계층별 폴더 분할 → 기능별로

FBA는 타입 기준으로 폴더를 나눈다:

```
app/admin/api/v1/sys/user.py
app/admin/crud/crud_user.py
app/admin/schema/user.py
app/admin/service/user_service.py
app/admin/model/user.py
```

사용자 기능 하나를 고치려면 **5개 폴더를 오간다.** 응집이 아니라 분산이다.

**기능(vertical slice) 기준으로 묶는다:**

```
modules/user/
├─ router.py
├─ schema.py
├─ service.py
├─ repository.py
└─ model.py
```

한 기능의 변경이 한 폴더에 갇힌다. 모듈 삭제가 폴더 삭제와 같아진다. 계층 규칙(`router → service → repository`)은 파일 단위로 그대로 유지된다.

---

## 4. 목표 디렉터리 구조

```
my-fastapi/
├─ pyproject.toml
├─ alembic.ini
├─ compose.yaml
├─ .importlinter
├─ migrations/
│  └─ versions/                 # 첫 커밋부터 채워져 있다
├─ tests/
│  ├─ unit/ integration/ e2e/
│  ├─ conftest.py
│  └─ factories.py
└─ src/app/
   ├─ main.py                   # create_app() 호출만
   ├─ core/                     # 무의존 계층
   │  ├─ config.py              #   pydantic-settings
   │  ├─ constants.py
   │  └─ paths.py
   ├─ common/                   # core만 import
   │  ├─ db/                    #   session, base model, mixin, soft_delete
   │  ├─ cache/                 #   redis 헬퍼
   │  ├─ security/              #   token encode/decode, hashing — 도메인 무지
   │  ├─ errors/                #   예외 타입 + 에러 코드
   │  ├─ pagination.py
   │  ├─ response.py
   │  └─ observability/         #   otel, prometheus
   ├─ modules/                  # common, core import
   │  ├─ auth/                  #   로그인, 토큰 발급, 세션 저장소
   │  ├─ user/
   │  ├─ rbac/
   │  └─ audit/                 #   opera log 소비자
   └─ bootstrap/                # 조립. 전부 import 가능
      ├─ app.py                 #   create_app()
      ├─ lifespan.py            #   자원 생성/정리
      ├─ middleware.py
      ├─ router.py
      └─ exception_handlers.py
```

**의존 방향:** `bootstrap → modules → common → core`. 역방향은 `lint-imports`가 CI에서 막는다.

---

## 5. 구축 순서

우선순위대로. 각 단계가 다음 단계의 전제다.

### Phase 1 — 뼈대 (여기서 타협하면 나중에 못 고친다)
- [ ] `core/config.py` — pydantic-settings, `.env.example` 동기화
- [ ] `bootstrap/lifespan.py` — 엔진·Redis를 여기서만 생성. **전역 인스턴스 0개**
- [ ] `common/db/session.py` — `SessionDep` / `TxDep`
- [ ] alembic 초기화 + **첫 리비전 커밋**. `create_all` 없음
- [ ] `.importlinter` + CI 연결
- [ ] `tests/conftest.py` — testcontainers, 트랜잭션 롤백 격리
- [ ] ruff + pre-commit + CI

### Phase 2 — 공용 계층
- [ ] `common/db/` base model, `DateTimeMixin`, `SoftDeleteMixin`(deleted=id 방식)
- [ ] `common/db/soft_delete.py` — `do_orm_execute` 전역 필터
- [ ] `common/errors/` — 에러 **코드** 체계, 예외 핸들러, i18n 카탈로그
- [ ] `common/response.py` — msgspec 응답, 표준 래퍼
- [ ] `common/security/` — 토큰 encode/decode만. 도메인 import 금지

### Phase 3 — 첫 모듈 (`user`)로 패턴 확정
- [ ] `modules/user/` 5파일 전부 + **테스트 3종(unit/integration/e2e) 동시에**
- [ ] 여기서 확정된 모양이 이후 모든 모듈의 템플릿이 된다

### Phase 4 — 인증/인가
- [ ] `modules/auth/` — 로그인, 리프레시, `UserSessionStore`(캐시 무효화 단일 지점)
- [ ] `modules/rbac/` — 권한 검사를 `Depends`로
- [ ] 잠긴 계정·잠긴 역할의 즉시 무효화 테스트

### Phase 5 — 운영
- [ ] `common/observability/` — OTel, Prometheus
- [ ] `modules/audit/` — 감사 로그 (미들웨어는 큐에 넣기만)
- [ ] compose: `migrate` → `api` 순서 보장
- [ ] CI 커버리지 게이트

---

## 6. 지켜야 할 규칙 요약

| # | 규칙 | 강제 수단 |
|---|---|---|
| 1 | `service`/`repository`에서 `commit()` 금지 | 코드리뷰 + grep 체크 |
| 2 | 의존 방향은 `bootstrap → modules → common → core` | `lint-imports` (CI) |
| 3 | 모듈 최상위에서 I/O 자원 생성 금지 | 리뷰 + import 테스트 |
| 4 | 앱 코드에서 `create_all()` 금지 | `alembic check` (CI) |
| 5 | 서비스 시그니처에 `Request`/`Response` 금지 | 코드리뷰 |
| 6 | 소프트 삭제 필터는 손으로 쓰지 않는다 | ORM 이벤트가 전역 처리 |
| 7 | 에러는 메시지가 아니라 코드로 raise | 예외 타입이 `code` 요구 |
| 8 | 새 모듈은 테스트와 같은 PR에 | 커버리지 게이트 |
| 9 | 함수 내부 `import`는 순환참조 신호 — 금지 | `lint-imports`가 근본 차단 |
| 10 | 파일 400줄 상한 | 리뷰 |

---

## 부록: FBA 리뷰 결론

| 항목 | 평가 | my-fastapi 대응 |
|---|---|---|
| 구조 일관성 | 상 | 유지 (§1.2) |
| 트랜잭션 설계 | 상 | 유지 (§1.1) |
| stateless service | 적절 | 유지 (§1.3) |
| 의존성 방향 | 하 — 양방향/순환 | 4단 계층 + CI 강제 (§2.2) |
| 자원 라이프사이클 | 하 — import 부작용 | lifespan + app.state (§2.1) |
| 스키마 관리 | 하 — 마이그레이션 0개 | alembic 단일 소스 (§2.3) |
| 테스트 커버리지 | 최하 — 7줄 | 첫 커밋부터 3종 (§2.8) |
| 확장 모델 | 위험 — 런타임 설치 | 정적 모듈 (§3.1) |

**DI 컨테이너 부재는 결함이 아니다.** FastAPI 관례에 맞는 선택이고 그대로 간다. 진짜 문제는 서비스 싱글턴이 아니라 **I/O 자원이 import 시점에 생성되는 것**이었다.
