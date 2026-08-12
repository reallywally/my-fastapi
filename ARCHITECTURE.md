# my-fastapi 설계 문서

`fastapi-best-architecture`(이하 FBA) 코드 리뷰 결과를 바탕으로, **가져올 것 / 고칠 것 / 버릴 것**을 정리한 설계 기준서.

FBA는 "잘 만든 실무형 스캐폴딩"이지만 이름만큼의 아키텍처는 아니다. 구조 일관성과 트랜잭션 설계는 배울 만하고, 의존성 방향·자원 라이프사이클·테스트는 다시 짜야 한다.

**메인 비즈니스는 게시판이다** (§4). §1~3은 그 게시판을 얹을 뼈대에 대한 판단이다.

## 0. 범위 — API 서버만

**화면은 만들지 않는다.** 이 저장소는 JSON API 서버다. 프론트엔드는 나중에 별도 저장소에서 붙인다.

그래서 지금 하지 않는 것:
- 템플릿 렌더링(Jinja2), 정적 파일 서빙, 세션 쿠키 기반 로그인 → **JWT만**
- 서버 사이드 리다이렉트, 플래시 메시지, CSRF 토큰 폼

대신 지금 챙길 것 — 나중에 화면을 붙일 때 서버를 다시 안 고치려면 이건 처음부터 맞아야 한다:
- **OpenAPI 스키마가 계약이다.** `simplify_operation_ids`(§1.5)로 클라이언트 코드 생성이 깨지지 않게 유지한다
- **CORS 설정을 처음부터** 둔다. 허용 오리진은 설정값으로 (하드코딩 금지)
- **에러 응답 형태를 고정**한다 (§2.6의 에러 코드 + §5 `common/response.py`). 화면은 이 코드로 분기한다
- **페이지네이션 응답 형태를 고정**한다 (§4.3 커서 방식). 나중에 무한 스크롤로 바꿔도 서버는 그대로다
- 파일 업로드는 응답에 **접근 URL을 담는다**. 화면이 경로를 조립하게 만들지 않는다

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

## 4. 메인 도메인 — 게시판

여기까지는 뼈대 이야기였다. 이 절은 **이 서버가 실제로 무엇을 하는지**를 정의한다.

범위: 게시판(카테고리) → 게시글 → 댓글. 여기에 첨부파일·조회수·검색.

### 4.1 바운디드 컨텍스트로 묶는다

`board` / `post` / `comment`는 §3.4의 vertical slice 단위지만, **서로 독립적이지 않다.** 댓글은 글 없이 존재할 수 없고 글은 게시판 없이 존재할 수 없다. 셋을 `modules/` 아래 평평하게 두면 모듈 간 참조가 무질서해진다.

**하나의 컨텍스트 안에 슬라이스를 중첩한다.**

```
modules/board/
├─ __init__.py            # 컨텍스트 라우터 조립 (bootstrap이 이것만 본다)
├─ board/                 # 게시판 정의 — 관리자가 만든다
│  ├─ router.py schema.py service.py repository.py model.py deps.py
├─ post/                  # 게시글
│  ├─ router.py schema.py service.py repository.py model.py
│  └─ view_counter.py     #   조회수 버퍼 (§4.5)
├─ comment/               # 댓글
│  └─ router.py schema.py service.py repository.py model.py
└─ attachment/            # 첨부파일
   └─ router.py schema.py service.py repository.py model.py
```

**컨텍스트 내부 의존 방향도 단방향이다.** `attachment`·`comment` → `post` → `board`. 역방향 금지 — `post`는 자기 댓글 수를 알지만(§4.4) `comment` 모듈은 모른다.

```ini
# .importlinter — §2.2 계약에 추가
[importlinter:contract:board-internal]
name = Board context internal layers
type = layers
layers =
    app.modules.board.comment | app.modules.board.attachment
    app.modules.board.post
    app.modules.board.board
```

**다른 컨텍스트(`user`, `auth`)와의 관계:** `author_id` FK만 들고 간다. `user_service`를 호출하지 않는다. 목록에 작성자 이름이 필요하면 **repository가 조인**한다 — 같은 DB이므로 조인이 정상이고, 서비스끼리 부르기 시작하면 §1.3에서 못 푼다고 한 그 문제로 돌아간다.

### 4.2 모델

```python
# modules/board/board/model.py
class Board(Base, DateTimeMixin, SoftDeleteMixin):
    __tablename__ = 'board'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(50))          # URL 식별자: 'notice', 'free'
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    read_role: Mapped[str] = mapped_column(String(50), default='anonymous')
    write_role: Mapped[str] = mapped_column(String(50), default='member')
    allow_comment: Mapped[bool] = mapped_column(default=True)
    allow_attachment: Mapped[bool] = mapped_column(default=True)
    display_order: Mapped[int] = mapped_column(default=0)

    __table_args__ = (UniqueConstraint('slug', 'deleted'),)   # §1.4 — 삭제 후 slug 재사용 가능
```

```python
# modules/board/post/model.py
class Post(Base, DateTimeMixin, SoftDeleteMixin):
    __tablename__ = 'post'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey('board.id'))
    author_id: Mapped[int] = mapped_column(ForeignKey('user.id'))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    is_pinned: Mapped[bool] = mapped_column(default=False)     # 상단 고정 (§4.3)
    status: Mapped[PostStatus] = mapped_column(default=PostStatus.published)
    view_count: Mapped[int] = mapped_column(default=0)         # Redis에서 주기 반영 (§4.5)
    comment_count: Mapped[int] = mapped_column(default=0)      # 비정규화 (§4.4)

    __table_args__ = (
        Index('ix_post_list', 'board_id', 'deleted', 'id'),    # 목록 커서 (§4.3)
        Index('ix_post_author', 'author_id', 'deleted'),
    )
```

```python
# modules/board/comment/model.py
class Comment(Base, DateTimeMixin, SoftDeleteMixin):
    __tablename__ = 'comment'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey('post.id'))
    author_id: Mapped[int] = mapped_column(ForeignKey('user.id'))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey('comment.id'))
    path: Mapped[str] = mapped_column(String(64))              # '00000012.00000031'
    depth: Mapped[int] = mapped_column(default=0)              # 0 또는 1까지만
    content: Mapped[str] = mapped_column(Text)
    is_removed: Mapped[bool] = mapped_column(default=False)    # 묘비 (§4.7)

    __table_args__ = (Index('ix_comment_thread', 'post_id', 'path'),)
```

**`path`를 두는 이유:** 댓글 트리 정렬을 `ORDER BY path` 한 번으로 끝낸다. `parent_id`만 있으면 재귀 CTE를 돌리고 정렬을 앱에서 다시 해야 한다. **깊이는 1단(댓글/대댓글)으로 제한한다** — 무한 뎁스는 화면에서 감당이 안 되고, 서버에서 막지 않으면 데이터가 먼저 망가진다.

### 4.3 목록 조회 — keyset 페이지네이션

`OFFSET`을 쓰지 않는다.

- 게시판은 깊은 페이지가 흔하다. `OFFSET 100000`은 10만 행을 읽고 버린다
- 페이지를 넘기는 사이 새 글이 올라오면 항목이 **중복되거나 누락된다**

```python
# repository.py
stmt = (
    select(Post)
    .where(Post.board_id == board_id, Post.is_pinned.is_(False))
    .where(Post.id < cursor if cursor is not None else true())
    .order_by(Post.id.desc())
    .limit(size + 1)          # 한 개 더 읽어서 has_next 판정
)
```

- **고정글은 별도 쿼리**로 앞에 붙인다. 정렬 키에 `is_pinned`를 섞으면 커서가 깨진다
- **전체 개수(`total`)를 주지 않는다.** `has_next`와 `next_cursor`만. 대형 게시판에서 `COUNT(*)`는 매 요청 풀스캔이다
- `deleted` 조건은 쓰지 않는다 — §2.4의 전역 필터가 붙인다

```json
{ "items": [...], "next_cursor": 10432, "has_next": true }
```

이 응답 형태는 §0의 계약이다. 나중에 화면이 무한 스크롤이든 페이지 번호든 서버는 그대로 간다.

### 4.4 `comment_count` 비정규화

목록에서 글마다 댓글 수를 세면 N+1이다. `post.comment_count`를 들고, 댓글 생성·삭제와 **같은 트랜잭션**에서 갱신한다.

```python
@router.post('/posts/{post_id}/comments')
async def create_comment(db: TxDep, ...):   # TxDep — 두 쓰기가 하나의 트랜잭션 (§1.1)
    ...
```

```python
class CommentService:
    @staticmethod
    async def create(*, db: AsyncSession, post_id: int, actor_id: int, obj: CreateComment) -> int:
        post = await post_repo.get(db, post_id)
        if post is None:
            raise NotFoundError(code='post.not_found')
        comment = await comment_repo.insert(db, post_id=post_id, author_id=actor_id, ...)
        await post_repo.bump_comment_count(db, post_id, +1)
        return comment.id
```

`bump_comment_count`는 **`UPDATE post SET comment_count = comment_count + 1`** 로 쓴다. 앱에서 읽고 더해서 쓰면 동시 댓글에서 갱신이 유실된다.

드리프트는 생긴다고 전제한다. 야간 배치로 실제 카운트와 대조·보정하고, 이 배치는 테스트 대상이다.

### 4.5 조회수 — 읽기가 쓰기가 되면 안 된다

글을 볼 때마다 `UPDATE post SET view_count = view_count + 1`을 하면:
- 인기 글 **한 행에 UPDATE가 몰려** row lock 경합이 난다
- **읽기 요청이 쓰기 트랜잭션이 된다.** §1.1에서 `SessionDep`/`TxDep`을 나눈 의미가 사라진다

**Redis에 누적하고 주기적으로 반영한다.**

```python
# modules/board/post/view_counter.py
class PostViewCounter:
    async def hit(self, redis: Redis, post_id: int, viewer_key: str) -> None:
        """중복 조회는 세지 않는다 (viewer 기준 10분)."""
        if not await redis.set(f'post:viewed:{post_id}:{viewer_key}', 1, ex=600, nx=True):
            return
        await redis.hincrby('post:views:pending', str(post_id), 1)

    async def flush(self, db: AsyncSession, redis: Redis) -> int:
        """pending 해시를 원자적으로 비우고 DB에 일괄 반영한다."""

post_views = PostViewCounter()
```

- 상세 조회 엔드포인트는 **`SessionDep`(트랜잭션 없음)을 유지**한다
- `flush`는 백그라운드 소비자가 주기 실행한다. 미들웨어에 넣지 않는다 — §2.2의 "미들웨어는 큐에 넣기만" 과 같은 결
- **Redis가 죽어도 조회는 성공해야 한다.** 카운팅 실패는 삼키고 로그만 남긴다. 조회수는 요청을 실패시킬 만한 값이 아니다
- 응답에는 DB의 `view_count`만 쓴다. pending을 합산해 정확하게 보이려는 유혹을 참는다

### 4.6 권한 — 게시판마다 다르다

`Board`가 `read_role` / `write_role`을 갖는다. **게시판 단위 검사는 `Depends`로** (§1.3의 "횡단 관심사만 Depends" 에 해당).

```python
# modules/board/board/deps.py
def require_board(perm: Literal['read', 'write']):
    async def _dep(db: SessionDep, actor: CurrentUserDep, slug: str) -> Board:
        board = await board_repo.get_by_slug(db, slug)
        if board is None:
            raise NotFoundError(code='board.not_found')
        required = board.read_role if perm == 'read' else board.write_role
        if not rbac.satisfies(actor, required):
            raise ForbiddenError(code='board.forbidden')
        return board
    return _dep

BoardReadDep = Annotated[Board, Depends(require_board('read'))]
BoardWriteDep = Annotated[Board, Depends(require_board('write'))]
```

**소유권 검사는 서비스에서** 한다. "작성자 본인 또는 관리자"는 대상 행을 읽어야 판정되는 업무 규칙이다.

```python
class PostService:
    @staticmethod
    async def update(*, db: AsyncSession, post_id: int, actor_id: int,
                     is_admin: bool, obj: UpdatePost) -> None:
        post = await post_repo.get(db, post_id)
        if post is None:
            raise NotFoundError(code='post.not_found')
        if post.author_id != actor_id and not is_admin:
            raise ForbiddenError(code='post.not_owner')
        ...
```

> **§2.7의 FBA 버그가 정확히 이 모양에서 났다.** 서비스가 `Request`를 받으면 `request.user.id`와 비교해야 할 것을 조회한 행의 `id`와 비교하기 쉽고, 그러면 조건이 상수가 되어 권한 검사가 통째로 죽는다. 비교 대상은 **라우터가 넘긴 `actor_id`** 다. 서비스는 `Request`를 모른다.
>
> 이 케이스는 e2e 테스트로 못 박는다: *타인의 글을 수정 시도 → 403*.

### 4.7 삭제 — 댓글이 달린 글

soft delete(§1.4, §2.4)라 행은 남는다. 문제는 트리가 끊기는 경우다.

| 상황 | 처리 |
|---|---|
| 글 삭제 | `post.deleted = post.id`. 댓글은 손대지 않는다 — 글이 안 보이면 댓글로 가는 경로가 없다 |
| 자식 없는 댓글 삭제 | `comment.deleted = comment.id`. 전역 필터가 감춘다 |
| **자식 있는 댓글 삭제** | 감추면 대댓글이 고아가 된다. `is_removed = True` + 본문 마스킹. **soft delete 아님** |

세 번째가 핵심이다. `deleted`(감사·복구용)와 `is_removed`(트리 유지용 묘비)는 **다른 개념이고 합치면 안 된다.** 하나로 합치는 순간 §2.4의 전역 필터가 자식까지 숨겨버린다.

응답에서 `is_removed` 댓글은 `content`를 비우고 작성자를 익명화한다. 마스킹은 **schema 계층**에서 한다 — 서비스가 응답 형태를 신경 쓰기 시작하면 §2.7 누수와 같은 문제다.

### 4.8 검색

`LIKE '%키워드%'`는 인덱스를 못 탄다. Postgres FTS로 시작한다.

```python
search_vector: Mapped[str] = mapped_column(
    TSVECTOR,
    Computed("to_tsvector('simple', title || ' ' || content)", persisted=True),
)
# Index('ix_post_search', 'search_vector', postgresql_using='gin')
```

- 설정은 `simple`로 시작한다. 한국어 형태소 분석이 실제로 필요해지면 그때 별도 검색엔진을 붙인다 — §3.1과 같은 원칙: **필요해진 뒤에, 정적으로**
- 생성 컬럼이므로 앱이 인덱싱 타이밍을 신경 쓸 필요가 없다
- 검색도 §4.3의 커서 페이지네이션을 쓴다. 랭킹 정렬이 필요해지면 커서 키가 `(rank, id)` 복합이 된다

### 4.9 첨부파일

§2.7 규칙: **서비스는 `UploadFile`을 받지 않는다.**

```python
@router.post('/posts/{post_id}/attachments')
async def upload(post_id: int, file: UploadFile, db: TxDep,
                 actor: CurrentUserDep, storage: StorageDep):
    stored = await storage.save(file)                       # 라우터가 저장까지 끝낸다
    return await attachment_service.attach(
        db=db, post_id=post_id, actor_id=actor.id,
        filename=file.filename, size=stored.size, key=stored.key,   # 원시 타입만 넘어간다
    )
```

- 저장소는 인터페이스로 두고 로컬/S3 구현을 교체한다. 인스턴스는 `lifespan`이 `app.state`에 넣는다 (§2.1)
- **고아 파일 문제:** 업로드는 성공했는데 글 저장이 실패하면 파일만 남는다. `attachment.post_id`를 nullable로 두고 미연결 파일을 배치로 정리한다
- 응답에 **접근 URL을 담는다** (§0). 화면이 경로를 조립하게 만들지 않는다
- 확장자·MIME·크기 제한은 라우터에서 검증한다. 저장 파일명은 서버가 정한다 (원본 이름을 경로에 쓰지 않는다)

### 4.10 API 형태

```
GET    /api/v1/boards                                게시판 목록
GET    /api/v1/boards/{slug}/posts?cursor=&size=     글 목록 (keyset)      [read_role]
GET    /api/v1/boards/{slug}/posts/search?q=         검색                  [read_role]
POST   /api/v1/boards/{slug}/posts                   글 작성               [write_role]
GET    /api/v1/posts/{id}                            글 상세 (+조회수)     [read_role]
PATCH  /api/v1/posts/{id}                            글 수정               [본인/관리자]
DELETE /api/v1/posts/{id}                            글 삭제               [본인/관리자]
POST   /api/v1/posts/{id}/attachments                첨부 업로드           [본인]
GET    /api/v1/posts/{id}/comments                   댓글 트리
POST   /api/v1/posts/{id}/comments                   댓글 작성             [write_role]
PATCH  /api/v1/comments/{id}                         댓글 수정             [본인]
DELETE /api/v1/comments/{id}                         댓글 삭제             [본인/관리자]
```

글 작성은 `slug` 하위에서 하지만 **조회·수정은 `/posts/{id}`로 평평하게** 간다. 글 id가 전역 유일한데 board를 다시 태우면 경로만 길어지고 검증만 늘어난다.

---

## 5. 목표 디렉터리 구조

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
   │  ├─ board/                 #   ★ 메인 도메인 (§4)
   │  │  ├─ board/              #     게시판 정의
   │  │  ├─ post/               #     게시글 + view_counter.py
   │  │  ├─ comment/            #     댓글
   │  │  └─ attachment/         #     첨부파일
   │  └─ audit/                 #   opera log 소비자
   └─ bootstrap/                # 조립만. 아래를 전부 import 가능 (composition root)
      ├─ app.py                 #   create_app() — FastAPI 인스턴스 생성 + 아래를 순서대로 등록
      ├─ lifespan.py            #   엔진·Redis·스토리지 생성/정리. 전역 인스턴스 0개 (§2.1)
      ├─ middleware.py          #   CORS, 로깅, 요청 ID
      ├─ router.py              #   각 모듈 라우터를 include_router로 수집
      └─ exception_handlers.py  #   에러 코드 → 응답 렌더링 (§2.6)
```

**의존 방향:** `bootstrap → modules → common → core`. 역방향은 `lint-imports`가 CI에서 막는다.

**`bootstrap`은 왜 별도 계층인가.** 조립을 `main.py`에 몰면 신 파일이 되고, 각 모듈이 자기를 등록하게 하면 모듈이 앱 객체를 알아야 해서 의존이 역류한다. **"아래를 전부 아는 층"을 딱 하나 두고 거기서만 조립한다.** 그래서 `bootstrap`에는 업무 로직이 0인 게 정상이다.

**`modules/board/` 내부에도 계층이 있다:** `comment`·`attachment` → `post` → `board` (§4.1). 이것도 `lint-imports` 계약으로 강제한다.

---

## 6. 구축 순서

우선순위대로. 각 단계가 다음 단계의 전제다. **Phase 5가 목적지고 1~4는 거기까지 가는 길이다.**

### Phase 1 — 뼈대 (여기서 타협하면 나중에 못 고친다)
- [ ] `core/config.py` — pydantic-settings, `.env.example` 동기화
- [ ] `bootstrap/lifespan.py` — 엔진·Redis를 여기서만 생성. **전역 인스턴스 0개**
- [ ] `common/db/session.py` — `SessionDep` / `TxDep`
- [ ] alembic 초기화 + **첫 리비전 커밋**. `create_all` 없음
- [ ] `.importlinter` + CI 연결
- [ ] `bootstrap/middleware.py` — CORS(허용 오리진은 설정값), 요청 ID (§0)
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

### Phase 5 — 게시판 (§4) ★메인 비즈니스
순서가 곧 의존 방향이다. 각 항목은 테스트와 같은 PR로 간다.

- [ ] `board/board/` — 게시판 CRUD, `slug` 조회, `require_board` deps (§4.6)
- [ ] `board/post/` — 작성/수정/삭제 + **keyset 목록** (§4.3)
  - [ ] 소유권 검사 e2e: *타인 글 수정 → 403* (§4.6의 FBA 버그 재발 방지)
  - [ ] 커서 페이지네이션: 페이징 중 새 글이 들어와도 중복·누락 없음
- [ ] `board/comment/` — `path` 기반 트리, 깊이 1단 제한 (§4.2)
  - [ ] `comment_count` 갱신이 같은 트랜잭션인지 (§4.4) — 롤백 시 카운트도 롤백
  - [ ] 자식 있는 댓글 삭제 → `is_removed` 묘비, 자식은 계속 보임 (§4.7)
- [ ] `board/post/view_counter.py` — Redis 버퍼 + flush 소비자 (§4.5)
  - [ ] 상세 조회가 여전히 `SessionDep`인지 (쓰기 트랜잭션이 아님)
  - [ ] **Redis 다운 시에도 조회 200** — fake redis로 예외 주입
- [ ] `board/attachment/` — 라우터가 `UploadFile` 처리, 서비스는 원시 타입만 (§4.9)
- [ ] FTS 검색 + GIN 인덱스 (§4.8)
- [ ] `comment_count` 정합성 보정 배치 + 고아 첨부 정리 배치

### Phase 6 — 운영
- [ ] `common/observability/` — OTel, Prometheus
- [ ] `modules/audit/` — 감사 로그 (미들웨어는 큐에 넣기만)
- [ ] compose: `migrate` → `api` 순서 보장
- [ ] CI 커버리지 게이트

---

## 7. 지켜야 할 규칙 요약

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
| 11 | 목록은 `OFFSET` 금지 — 커서만 | 리뷰 + 응답 스키마에 `total` 없음 |
| 12 | 읽기 엔드포인트에 `TxDep` 금지 | 리뷰 (조회수는 Redis 경유 §4.5) |
| 13 | 카운터는 `SET x = x + 1`. 앱에서 읽고 쓰지 않는다 | 리뷰 |
| 14 | 소유권 비교는 라우터가 넘긴 `actor_id`로만 | e2e 테스트 (타인 글 수정 → 403) |
| 15 | `board` 컨텍스트 내부 방향: comment/attachment → post → board | `lint-imports` (CI) |
| 16 | 화면용 코드 없음 — 템플릿·정적파일·세션쿠키 금지 | 리뷰 (§0) |

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
