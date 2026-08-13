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
- **에러 응답 형태를 고정**한다 (§2.6의 에러 코드 + §6 `common/response.py`). 화면은 이 코드로 분기한다
- **페이지네이션 응답 형태를 고정**한다 (§4.3 커서 방식). 나중에 무한 스크롤로 바꿔도 서버는 그대로다
- 파일 업로드는 응답에 **접근 URL을 담는다**. 화면이 경로를 조립하게 만들지 않는다

---

## 1. 가져올 것 (검증된 부분)

### 1.1 트랜잭션 경계를 DI로 처리

FBA에서 가장 잘 된 부분. **service/repository는 절대 `commit()` 하지 않는다.** 트랜잭션은 엔드포인트가 어떤 의존성을 선언했는지로 결정된다.

```python
# common/db/deps.py
async def get_db(request: Request) -> AsyncGenerator[AsyncConnection, None]:
    async with source(request)() as conn:            # 읽기: 열고, 끝나면 무조건 롤백
        transaction = await begin(conn)
        try:
            yield conn
        finally:
            await transaction.rollback()

async def get_db_tx(request: Request) -> AsyncGenerator[AsyncConnection, None]:
    async with source(request)() as conn:            # 쓰기: 커밋 또는 롤백
        transaction = await begin(conn)
        try:
            yield conn
        except BaseException:
            await transaction.rollback()
            raise
        else:
            await transaction.commit()

ConnDep = Annotated[AsyncConnection, Depends(get_db)]
TxDep = Annotated[AsyncConnection, Depends(get_db_tx)]
```

```python
@router.get('/{pk}')
async def get_user(db: ConnDep, pk: int): ...           # 읽기: 끝나면 롤백

@router.post('')
async def create_user(db: TxDep, obj: CreateUserRequest): ...  # 쓰기: 자동 커밋/롤백
```

**읽기도 트랜잭션 안에서 돈다.** 한 요청이 두 번 조회하는 사이에 남의 커밋이 끼어들면 같은 요청 안에서 앞뒤가 다른 데이터를 본다. 끝에 롤백하는 것은 두 가지를 동시에 준다 — 요청 하나가 일관된 스냅샷을 보고, 읽기 의존성으로 들어온 쓰기는 밖으로 나가지 않는다.

`begin()`은 **이미 트랜잭션이 열려 있으면 SAVEPOINT를 만든다.** 운영에서는 갓 빌린 연결이라 항상 바깥 트랜잭션이고, 테스트에서는 연결이 이미 바깥 트랜잭션 안에 있어서 (§2.8) SAVEPOINT가 된다 — 요청이 커밋해도 테스트가 통째로 롤백할 수 있는 이유다.

**규칙 (린트로 강제):**
- `service/`, `repository/` 안에서 `commit()` 금지.
- 예외는 그대로 전파되어 DI가 롤백한다.

> FBA는 이 규칙을 21,000줄 전체에서 지켰다 (`commit()` 호출 0회). 그대로 채택.

### 1.2 계층 네이밍과 역할 분리

| 역할 | FBA | my-fastapi | 책임 |
|---|---|---|---|
| 입출력 | `api` | `router.py` | HTTP만. 검증·직렬화·상태코드 |
| 전송 객체 | `schema` | `schema.py` | Pydantic. 요청/응답 계약 |
| 업무 규칙 | `service` | `service.py` | 트랜잭션 내부 로직, 도메인 규칙 |
| 데이터 접근 | `crud` | `repository.py` | 쿼리만. 규칙 없음 |
| 외부 서버 | — | `gateway.py` | HTTP 호출 + 응답을 우리 타입으로 변환 (§5.5) |
| 테이블 | `model` | `model.py` | Core `Table` 정의 + 행 dataclass (§1.6) |

`crud` → `repository`로 개명. `crud`는 CRUD 5개만 있다는 인상을 주는데 실제로는 모든 쿼리가 여기 산다.

**DTO 이름이 방향을 말한다.** 받는 것은 `~Request`, 내보내는 것은 `~Response`다 — `CreateUserRequest`, `UpdateUserRequest`, `UserResponse`. `UserOut` 같은 중립적인 이름은 들어오는 것인지 나가는 것인지 라우터를 열어봐야 알 수 있다. 다른 DTO 안에만 들어가는 조각과 공통 베이스는 밑줄로 시작한다 — 규칙 #26의 wire DTO와 같은 표시로, "단독으로 오가는 계약이 아니다"라는 뜻이다 (규칙 #32).

이 규칙은 `modules/*/schema.py`에만 적용된다. `common/`의 공용 봉투(`Page`, `CursorParams`)는 특정 요청·응답에 묶이지 않는 재사용 타입이고, `common/db/schema.py`는 이름만 같을 뿐 테이블 컬럼 팩토리다.

### 1.3 stateless service + 모듈 전역 인스턴스

**DI 컨테이너를 도입하지 않는다.** FBA 방식을 유지한다.

```python
class UserService:
    @staticmethod
    async def get(*, db: AsyncConnection, pk: int) -> User: ...

user_service = UserService()
```

이유:
- 서비스에 인스턴스 상태가 없다. 주입할 게 없는 객체를 wiring하는 건 보일러플레이트다.
- FastAPI의 `Depends`는 엔드포인트에서 시작하는 트리라 **service → service 호출을 풀 수 없다.** 해결하려면 `dependency-injector` 같은 컨테이너를 추가해야 하는데, 의존성 하나 줄이려고 프레임워크를 얹는 거래다.
- Python은 `unittest.mock.patch`로 모듈 참조를 교체할 수 있다. 전역 싱글턴이 곧 테스트 불가는 아니다.
- tiangolo의 `full-stack-fastapi-template`도 같은 패턴이다. 생태계 주류를 벗어날 이유가 없다.

**`Depends`는 요청 스코프 / 횡단 관심사에만 쓴다:** 인증, 인가, DB 연결, Redis, 레이트리밋, 페이지네이션, 현재 사용자.

### 1.4 soft delete에 삭제 행 id를 저장

FBA의 영리한 부분. `deleted`가 boolean이 아니라 `0` 또는 **자기 행의 id**다.

```python
Column('deleted', BigIntPK, default=0, server_default='0', nullable=False, index=True)
# UniqueConstraint('username', 'deleted')
```

boolean이면 `unique(username)` 때문에 삭제된 아이디를 재사용할 수 없다. id를 넣으면 복합 unique가 성립해서 재등록이 가능하다. **채택.**

### 1.5 인프라 스택

- **ruff** 촘촘하게 (`ANN`으로 타입 힌트 강제, `line-length=120`, `quote-style='single'`)
- **msgspec** 기반 `JSONResponse` — orjson보다 빠르고 Pydantic v2와 궁합이 좋다
- **OpenTelemetry + Prometheus** — 처음부터 붙인다. 나중에 붙이면 계측 지점을 놓친다
- **`ensure_unique_route_names` / `simplify_operation_ids`** — OpenAPI 클라이언트 생성 품질을 위해 유지

### 1.6 DB는 SQLite, ORM 없이 SQLAlchemy Core

**결정 두 개다.**

1. **DB는 SQLite + aiosqlite.** 서버 프로세스가 없고, 백업이 파일 복사고, 테스트가 Docker 없이 돈다. 게시판 하나 규모에서 Postgres를 세우는 비용이 이득보다 크다.
2. **ORM은 쓰지 않는다. SQLAlchemy Core만 쓴다.** `DeclarativeBase`도 `Session`도 없다. 테이블은 `Table`로 정의하고, 행은 dataclass로 받는다.

두 번째가 왜 Core냐 — raw SQL도 아니고 ORM도 아닌 자리다.

**ORM을 버리는 이유:**
- 나가는 SQL이 코드에 보인다. N+1과 예상 못 한 조인은 "언제 SQL이 나가는지 안 보이는" 데서 온다
- identity map · lazy loading · `expire_on_commit` 같은, 알아야 제대로 쓸 수 있는 개념이 사라진다
- 행 객체가 그냥 dataclass다. DB 없이 만들 수 있고, 고쳐도 DB에 반영되지 않는다 (그래서 수정은 반드시 레포지토리를 거친다)

**그런데 Core는 남기는 이유 — 방언 교체:**

지금은 SQLite지만 PostgreSQL이나 MySQL로 옮길 수 있어야 한다. 그 "옮길 수 있음"을 실제로 만들어주는 것이 Core다. 직접 만든 추상화로는 다음을 전부 소유해야 한다.

| 방언마다 다른 것 | Core가 해주는 것 |
|---|---|
| 파라미터 스타일 (`?` / `%(name)s` / `:name`) | 컴파일 시점에 방언별로 렌더링 |
| 식별자 인용 (`"user"` / `` `user` ``) | `Table`/`Column` 이름을 알아서 인용 |
| 자동 증가 PK (`INTEGER PK` / `BIGSERIAL` / `AUTO_INCREMENT`) | `BigIntPK` variant + `inserted_primary_key` |
| `RETURNING` 지원 여부 (MySQL은 **없다**) | `inserted_primary_key`가 lastrowid / RETURNING 중 맞는 것을 고른다 |
| `LIMIT` / `OFFSET` 문법, boolean 표현, 타입 이름 | 컴파일러가 흡수 |
| DDL 렌더링 | alembic이 방언별로 생성 |

**이식성이 사는 곳은 두 파일이다:**

- `common/db/types.py` — `BigIntPK`(자동 증가 PK), `UTCDateTime`(tz를 버리는 방언에서 UTC 보존)
- `common/db/engine.py` — SQLite PRAGMA와 명시적 `BEGIN`, 서버 DB의 커넥션 풀 설정

그 밖에서 방언 이름이 나오면 잘못 짠 것이다 (규칙 #18). 새는 통로는 둘뿐이고, 둘 다 테스트가 막는다: `sqlalchemy.dialects.*` import와 `text()` 원시 SQL.

**SQLite 기본값 중 조용히 틀리는 것들** — 전부 `common/db/engine.py`가 처리한다.

| SQLite 기본값 | 증상 | 대응 |
|---|---|---|
| `foreign_keys=OFF` | FK를 선언해도 아무 일도 안 일어난다. 데이터가 깨진 뒤에 안다 | PRAGMA로 ON |
| `journal_mode=delete` | 읽기가 쓰기에 막힌다 | WAL |
| `busy_timeout=0` | 동시 쓰기에서 즉시 `database is locked` | 5초 |
| 드라이버가 트랜잭션을 임의로 연다 | DDL 앞에서 커밋이 새고 SAVEPOINT가 어긋난다 → **테스트 롤백 격리(§2.8)와 `TxDep`의 중첩 트랜잭션(§1.1)이 깨진다** | `isolation_level=None` + 명시적 `BEGIN` |
| `BIGINT PRIMARY KEY`는 rowid 별칭이 아니다 | **자동 증가하지 않는다.** id를 손으로 안 넣으면 NULL | `BigIntPK` (sqlite variant로 INTEGER) |
| `DateTime(timezone=True)`가 naive를 돌려준다 | aware와 비교하는 순간 `TypeError` | `UTCDateTime` TypeDecorator |
| `ALTER TABLE`이 거의 없다 | 컬럼 변경·삭제 리비전이 실행 시점에 죽는다 | alembic `render_as_batch=True` |

**SQLite라서 지금 포기하는 것:**
- **§4.8 전문검색은 FTS5로 간다.** `TSVECTOR` + GIN은 없다. 이건 어떤 추상화로도 안 덮이는 자리고, 방언을 옮기면 다시 짜야 한다
- **쓰기는 한 번에 하나다.** WAL이 읽기를 풀어줄 뿐 쓰기 직렬화는 그대로다 → **§4.5 조회수 버퍼링이 선택이 아니라 필수가 된다**
- 수평 확장 불가. 필요해지는 시점이 곧 방언을 옮기는 시점이다
- snowflake PK 옵션은 접는다. 분산 쓰기가 없으면 의미가 없다

**"옮길 수 있다"를 주장이 아니라 사실로 유지하는 방법:**

`tests/unit/test_dialect_portability.py`가 레포지토리의 **모든 문장과 모든 `CREATE TABLE`을 sqlite/postgresql/mysql 세 방언으로 컴파일해본다.** 서버가 필요 없다 — SQLAlchemy는 드라이버 없이도 컴파일할 수 있고, 방언에 없는 구문은 거기서 터진다. 길이 없는 `String` 하나(MySQL은 `VARCHAR`에 길이가 필수다)가 나중에 이식을 막는 것도 여기서 잡힌다.

이게 못 잡는 것은 런타임 의미 차이(잠금, 격리 수준, 정렬·대소문자 비교, 타임존)다. 그건 실제로 그 DB로 한 번 돌려야 안다. **그래서 이 테스트는 하한선이지 보증이 아니다.** 실제로 옮기는 날 CI에 그 방언을 한 줄 추가해서 전체 스위트를 돌리는 것이 마지막 단계다.

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
    app.state.db_source = engine.connect   # 의존성이 연결을 빌리는 통로 (§1.1)
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
    async def update(*, db: AsyncConnection, redis: Redis, pk: int, obj: UpdateUserRequest) -> int: ...
```

얻는 것:
- `import`만으로는 아무 연결도 열리지 않는다 → 순수 유닛테스트 가능
- 테스트에서 `app.state.db_source`를 바꿔 끼우면 요청 전체가 한 트랜잭션에 묶인다 (§2.8)
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

**해결: 조건을 한 조각으로 만들고, 그 조각만 쓰게 강제한다.**

ORM 전역 필터(`do_orm_execute` + `with_loader_criteria`)를 쓰면 자동으로 붙지만, ORM을 걷어냈으므로 (§1.6) 그 자리를 다른 것으로 메워야 한다. 자동은 아니고 **한 곳**이다.

```python
# common/db/sql.py
def alive(model: type[SoftDeletable]) -> ColumnElement[bool]:
    return model.TABLE.c.deleted == 0

def select_alive(model: type[SoftDeletable]) -> Select:
    return select(*columns(model)).where(alive(model))       # 레포지토리는 여기서 출발한다

def soft_delete(model: type[SoftDeletable], *conditions) -> Update:
    # deleted = True 가 아니라 자기 id. 그리고 이미 지워진 행은 건드리지 않는다
    return update(model.TABLE).where(*conditions, alive(model)).values(deleted=model.TABLE.c.id)
```

- 조건이 바뀌면 고칠 곳이 한 군데다
- 삭제분까지 봐야 하면 `select_rows()`를 쓴다. 명시적으로 쓴 것만 예외가 된다
- **레포지토리가 `deleted == 0`을 손으로 쓰지 않는지 AST로 검사한다** (규칙 #6). 비교식을 찾는 것이지 문자열을 찾는 것이 아니다 — 독스트링에서 규칙을 설명하는 문장을 위반으로 잡으면 그건 규칙이 아니라 함정이다
- `SELECT` 목록은 `columns(model)`이 dataclass 필드에서 뽑는다. 모델과 쿼리가 어긋날 수가 없다

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
├─ conftest.py          # 임시 SQLite 파일 + fakeredis. Docker 불필요
├─ factories.py         # 테스트 데이터 팩토리
├─ unit/                # 순수 로직. DB·Redis 없음. 밀리초 단위
├─ integration/         # repository ↔ 실제 DB
└─ e2e/                 # httpx AsyncClient로 라우터 관통
```

```python
# conftest.py 핵심
@pytest.fixture(scope='session')
async def app(settings):                            # settings: 임시 디렉터리의 SQLite 파일
    app = create_app()
    app.state.engine = create_engine(settings)      # 앱과 같은 팩토리 (§1.6의 PRAGMA가 걸린다)
    app.state.db_source = app.state.engine.connect
    app.state.redis = FakeAsyncRedis(decode_responses=True)   # §2.1이 예고한 그대로
    await run_migrations(app.state.engine)          # create_all 아님. alembic으로
    yield app

@pytest.fixture
async def db_connection(app):                       # 테스트 하나를 감싸는 바깥 트랜잭션
    async with app.state.engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn                              # client 픽스처가 이 연결을 db_source 에 꽂는다
        finally:
            await transaction.rollback()
```

**Docker가 필요 없다.** DB는 임시 파일, Redis는 fakeredis. 진짜 Redis로 돌리려면 `TEST_REDIS_URL`을 준다.

- 각 테스트는 **트랜잭션 안에서 돌고 롤백**한다. 테스트 간 격리를 DB truncate로 하지 않는다. 요청이 여는 `TxDep` 트랜잭션은 그 안에서 SAVEPOINT가 된다 (§1.1) — 커밋해도 밖으로 안 나간다.
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

테이블은 `define_table()`이 만들고(공통 컬럼 자동), 행은 dataclass로 받는다 (§1.6).
`String`에는 **항상 길이를 준다** — MySQL은 인덱스가 걸리는 문자열에 길이가 필수다.

```python
# modules/board/board/model.py
board_table = define_table(
    'board',
    Column('slug', String(50), nullable=False),            # URL 식별자: 'notice', 'free'
    Column('name', String(100), nullable=False),
    Column('description', Text),
    Column('read_role', String(50), default='anonymous', nullable=False),
    Column('write_role', String(50), default='member', nullable=False),
    Column('allow_comment', Boolean, default=True, nullable=False),
    Column('allow_attachment', Boolean, default=True, nullable=False),
    Column('display_order', Integer, default=0, nullable=False),
    UniqueConstraint('slug', 'deleted'),                   # §1.4 — 삭제 후 slug 재사용 가능
)

@dataclass(slots=True)
class Board(SoftDeletable):
    TABLE: ClassVar[Table] = board_table

    slug: str
    name: str
    description: str | None
    read_role: str
    write_role: str
    allow_comment: bool
    allow_attachment: bool
    display_order: int
```

```python
# modules/board/post/model.py
post_table = define_table(
    'post',
    Column('board_id', BigIntPK, ForeignKey('board.id'), nullable=False),
    Column('author_id', BigIntPK, ForeignKey('user.id'), nullable=False),
    Column('title', String(200), nullable=False),
    Column('content', Text, nullable=False),
    Column('is_pinned', Boolean, default=False, nullable=False),          # 상단 고정 (§4.3)
    Column('status', Enum(PostStatus, native_enum=False, length=20), default=PostStatus.published),
    Column('view_count', Integer, default=0, nullable=False),             # Redis에서 주기 반영 (§4.5)
    Column('comment_count', Integer, default=0, nullable=False),          # 비정규화 (§4.4)
    Index('ix_post_list', 'board_id', 'deleted', 'id'),                   # 목록 커서 (§4.3)
    Index('ix_post_author', 'author_id', 'deleted'),
    # 전문검색은 별도 FTS5 가상 테이블이다 (§4.8). 여기에 컬럼을 두지 않는다.
)
```

```python
# modules/board/comment/model.py
comment_table = define_table(
    'comment',
    Column('post_id', BigIntPK, ForeignKey('post.id'), nullable=False),
    Column('author_id', BigIntPK, ForeignKey('user.id'), nullable=False),
    Column('parent_id', BigIntPK, ForeignKey('comment.id')),
    Column('path', String(64), nullable=False),                # '00000012.00000031'
    Column('depth', Integer, default=0, nullable=False),        # 0 또는 1까지만
    Column('content', Text, nullable=False),
    Column('is_removed', Boolean, default=False, nullable=False),  # 묘비 (§4.7)
    Index('ix_comment_thread', 'post_id', 'path'),
)
```

FK 컬럼에 `BigIntPK`를 쓰는 것에 주의한다. PK가 방언마다 다른 타입으로 렌더링되므로
(§1.6) FK도 같은 타입이어야 한다 — 다르면 제약 생성이 그 방언에서 실패한다.

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
    async def create(*, db: AsyncConnection, post_id: int, actor_id: int, obj: CreateComment) -> int:
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
- 인기 글 **한 행에 UPDATE가 몰려** 경합이 난다. **SQLite는 쓰기가 DB 전체에 하나뿐이라(§1.6) 이게 서버 전체를 막는다** — 선택이 아니라 필수인 이유
- **읽기 요청이 쓰기 트랜잭션이 된다.** §1.1에서 `ConnDep`/`TxDep`을 나눈 의미가 사라진다

**Redis에 누적하고 주기적으로 반영한다.**

```python
# modules/board/post/view_counter.py
class PostViewCounter:
    async def hit(self, redis: Redis, post_id: int, viewer_key: str) -> None:
        """중복 조회는 세지 않는다 (viewer 기준 10분)."""
        if not await redis.set(f'post:viewed:{post_id}:{viewer_key}', 1, ex=600, nx=True):
            return
        await redis.hincrby('post:views:pending', str(post_id), 1)

    async def flush(self, db: AsyncConnection, redis: Redis) -> int:
        """pending 해시를 원자적으로 비우고 DB에 일괄 반영한다."""

post_views = PostViewCounter()
```

- 상세 조회 엔드포인트는 **`ConnDep`(끝나면 롤백)을 유지**한다
- `flush`는 백그라운드 소비자가 주기 실행한다. 미들웨어에 넣지 않는다 — §2.2의 "미들웨어는 큐에 넣기만" 과 같은 결
- **Redis가 죽어도 조회는 성공해야 한다.** 카운팅 실패는 삼키고 로그만 남긴다. 조회수는 요청을 실패시킬 만한 값이 아니다
- 응답에는 DB의 `view_count`만 쓴다. pending을 합산해 정확하게 보이려는 유혹을 참는다

### 4.6 권한 — 게시판마다 다르다

`Board`가 `read_role` / `write_role`을 갖는다. **게시판 단위 검사는 `Depends`로** (§1.3의 "횡단 관심사만 Depends" 에 해당).

```python
# modules/board/board/deps.py
def require_board(perm: Literal['read', 'write']):
    async def _dep(db: ConnDep, actor: CurrentUserDep, slug: str) -> Board:
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
    async def update(*, db: AsyncConnection, post_id: int, actor_id: int,
                     is_admin: bool, obj: UpdatePostRequest) -> None:
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
| 자식 없는 댓글 삭제 | `comment.deleted = comment.id`. `alive()`가 감춘다 |
| **자식 있는 댓글 삭제** | 감추면 대댓글이 고아가 된다. `is_removed = True` + 본문 마스킹. **soft delete 아님** |

세 번째가 핵심이다. `deleted`(감사·복구용)와 `is_removed`(트리 유지용 묘비)는 **다른 개념이고 합치면 안 된다.** 하나로 합치는 순간 §2.4의 `alive()`가 자식까지 숨겨버린다.

응답에서 `is_removed` 댓글은 `content`를 비우고 작성자를 익명화한다. 마스킹은 **schema 계층**에서 한다 — 서비스가 응답 형태를 신경 쓰기 시작하면 §2.7 누수와 같은 문제다.

### 4.8 검색

`LIKE '%키워드%'`는 인덱스를 못 탄다. **SQLite FTS5**를 쓴다 — §1.6에서 Postgres를 접었으므로 `TSVECTOR` + GIN은 없다.

`TSVECTOR` 생성 컬럼과 결정적으로 다른 점: FTS5는 **별도 가상 테이블**이라 원본과 자동으로 동기화되지 않는다.

```sql
CREATE VIRTUAL TABLE post_fts USING fts5(
    title, content,
    content='post', content_rowid='id',   -- external content: 본문을 중복 저장하지 않는다
    tokenize='unicode61'
);
```

동기화는 **트리거로 DB에 맡긴다.** 앱이 인덱싱을 기억해야 하면 §2.4의 `deleted=0`과 같은 실수가 반복된다 — 어딘가에서 반드시 빠뜨린다.

```sql
CREATE TRIGGER post_fts_insert AFTER INSERT ON post BEGIN
    INSERT INTO post_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
-- update / delete 트리거도 같이. external content 는 삭제 시 'delete' 명령 행을 넣어야 한다
```

- 가상 테이블과 트리거는 alembic이 autogenerate하지 못한다. **리비전을 손으로 쓴다** — 그래서 §2.3의 "마이그레이션이 유일한 소스" 가 여기서 더 중요해진다
- 토크나이저는 `unicode61`로 시작한다. **한국어 형태소 분석은 안 된다** — 어절 단위 매칭이다. 실제로 부족해지면 그때 검색엔진을 붙인다 (§3.1과 같은 원칙: 필요해진 뒤에, 정적으로)
- **soft delete와 FTS는 자동으로 연결되지 않는다.** `alive()`(§2.4)는 우리 테이블의 조건이라 가상 테이블에 붙지 않는다. FTS 결과를 `post`와 조인해서 걸러야 한다
- 검색도 §4.3의 커서 페이지네이션을 쓴다. `rank` 정렬이 필요해지면 커서 키가 `(rank, id)` 복합이 된다

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

## 5. 외부 서버 호출 (upstream)

다른 서버에 HTTP 요청을 보내야 한다. **서버는 A, B, … n개로 늘어난다.**

그래서 서버마다 클래스를 만들지 않는다. 3개째부터 복사-붙여넣기가 되고, 타임아웃 설정을 한 곳에서 빠뜨린다. **`이름 → 설정` 맵 하나**로 두고, 새 서버는 설정 한 줄로 붙인다.

```
UPSTREAMS={"a":{"base_url":"https://a.example.com"},
           "b":{"base_url":"https://b.example.com","read_timeout_seconds":10}}
```

코드 변경은 없다. §3.1과 같은 원칙 — 확장은 런타임 설치가 아니라 **설정과 배포**로.

### 5.1 두 계층으로 쪼갠다

한 파일에 다 넣으면 "재시도 로직"과 "A 서버의 404가 무슨 뜻인가"가 섞인다. 전자는 모든 서버에 같고, 후자는 서버마다 다르다.

| 계층 | 파일 | 아는 것 | 모르는 것 |
|---|---|---|---|
| 전송 | `common/http/client.py` | 타임아웃, 재시도, 커넥션 격리, 요청 ID 전파 | **상대가 무슨 서버인지** |
| 어댑터 | `modules/*/gateway.py` | 경로, 응답 모양, 상태코드의 의미 | 재시도를 몇 번 하는지 |

`common`이 도메인을 모른다는 §2.2가 여기서도 성립한다. `lint-imports`가 막는다.

### 5.2 타임아웃은 4종 전부 명시한다

```python
httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)
```

**`pool`을 빼먹는 것이 가장 위험하다.** 커넥션 풀 대기에 상한이 없으면, 느려진 업스트림 앞에 우리 요청이 무한정 줄을 선다. 워커가 전부 점유되고 **우리 서버가 같이 죽는다** — 상대의 장애가 우리 장애가 되는 경로다.

`max_connections`를 업스트림별로 두는 이유도 같다(bulkhead). A가 느려져도 B 호출이 커넥션을 얻을 수 있어야 한다.

### 5.3 재시도는 멱등한 것만

| 대상 | 재시도 | 이유 |
|---|---|---|
| GET/HEAD/OPTIONS/PUT/DELETE | ○ | HTTP 의미상 멱등 |
| **POST/PATCH** | **×** | 타임아웃은 "처리되지 않았다"가 아니라 **"결과를 못 봤다"**다. 재시도하면 두 번 처리될 수 있다 |
| 429, 502, 503, 504 | ○ | 일시적 과부하 |
| **500** | **×** | 보통 상대의 버그다. 재시도는 장애 중인 서버에 부하만 보탠다 |
| 그 외 4xx | × | 상대가 확정된 답을 줬다 |

POST를 재시도해야 하면 호출자가 `idempotent=True`로 **명시**한다. 기본값이 안전한 쪽이어야 한다.

백오프에는 지터를 섞는다. 없으면 여러 워커가 같은 시점에 동시에 재시도해서 방금 살아난 서버를 다시 넘어뜨린다. 상대가 준 `Retry-After`는 5초로 캡한다 — "10분 뒤에 오라"를 그대로 믿으면 요청이 매달린다.

### 5.4 실패를 네 가지로 나눈다

| 예외 | 우리 응답 | 무슨 일인가 | 누가 고치나 |
|---|---|---|---|
| `UpstreamTimeoutError` | 504 | 재시도해도 응답이 없다 | 상대 (또는 타임아웃 값) |
| `UpstreamUnavailableError` | 503 | 연결 불가, 또는 429/503 지속 | 상대 |
| `UpstreamStatusError` | 502 | 2xx가 아닌 확정 응답 | **gateway가 의미를 정한다** |
| `UpstreamPayloadError` | 502 | 응답이 왔지만 아는 모양이 아니다 | 상대가 계약을 바꿨다 |

**상대의 상태코드를 우리 응답으로 그대로 흘리지 않는다.** 상대가 404를 줬다고 우리가 404를 주면, 클라이언트는 우리 리소스가 없는 건지 남의 리소스가 없는 건지 구분할 수 없다. 의미 부여는 gateway가 한다:

```python
except UpstreamStatusError as exc:
    if exc.upstream_status == 404:
        raise NotFoundError(code='weather.city_unknown') from exc
    raise                          # 나머지는 502/503으로
```

마지막 줄이 중요하다. **`upstream.bad_payload`를 500과 구분하는 이유**도 같다 — 우리 버그는 코드를 고치고, 상대의 변경은 어댑터를 고치거나 상대에게 연락한다. 로그에서 갈라져야 대응이 갈라진다.

어느 업스트림이 실패했는지는 **로그에만** 남긴다. 응답 본문에 넣으면 내부 구조가 드러난다 (규칙 #20).

### 5.5 DTO 세 종류를 섞지 않는다 ★

이게 이 절의 핵심이다. 데이터가 세 가지 있고, 소유자가 다르다.

| | 무엇 | 소유자 | 바뀌면 |
|---|---|---|---|
| `model.py` | 우리 테이블의 행 | **우리** | 마이그레이션을 쓴다 (§2.3) |
| `schema.py` | **우리** API 계약 | **우리** | 화면이 깨진다 (§0) |
| `gateway.py`의 wire DTO | **남의** API 응답 | **상대** | 예고 없이 바뀐다 |

세 번째가 문제다. 상대의 응답을 그대로 쓰면:

- **응답으로 흘리면** 우리 API가 상대의 필드명에 묶인다. 상대가 `cityName`을 `city_name`으로 바꾸면 **우리 클라이언트가 깨진다.** 우리가 통제하지 못하는 계약이 된다.
- **DB에 그대로 저장하면** 상대의 스키마가 우리 테이블 스키마가 된다. 마이그레이션 히스토리가 남의 릴리스에 끌려간다.

**규칙: wire DTO는 `gateway.py` 밖으로 나가지 않는다.** gateway의 반환 타입은 모듈이 정의한 타입이다. 상대가 응답을 바꾸면 고칠 파일이 정확히 하나다.

```python
class _WeatherPayload(BaseModel):                  # wire DTO — 밑줄로 시작한다
    model_config = ConfigDict(extra='ignore')      # 상대가 필드를 더해도 안 깨진다
    city_name: str = Field(alias='cityName')       # 이름 변환은 여기 한 곳
    temp_c: float = Field(alias='temperatureCelsius')

@dataclass(frozen=True, slots=True)
class Weather:                                     # 우리 어휘. 이것만 밖으로 나간다
    city: str
    celsius: float

class WeatherGateway(Gateway):
    upstream = 'weather'                           # 설정의 키

    @classmethod
    async def fetch(cls, *, upstreams: UpstreamRegistry, city: str) -> Weather:
        response = await cls.client(upstreams).request('GET', '/weather', params={'city': city})
        payload = cls.parse(response, _WeatherPayload)
        return Weather(city=payload.city_name, celsius=payload.temp_c)
```

`extra='ignore'`가 기본이다. 상대가 필드를 추가하는 것은 흔한 일이고, 그걸로 우리가 깨지면 안 된다. 반대로 **필드가 사라지거나 타입이 바뀌면 실패해야 한다** — 조용히 `None`이 흘러들어가는 것보다 낫다.

**그래서 service가 보는 것은 우리 타입뿐이다.** DB에서 왔는지 남의 서버에서 왔는지 몰라도 된다. 저장이 필요하면 service가 model로 옮긴다.

### 5.6 gateway는 service처럼 stateless다

`db`, `redis`를 인자로 받는 것과 같은 방식으로 `upstreams`를 받는다 (§2.1, §1.3).

```python
class PostService:
    @staticmethod
    async def create(*, db: AsyncConnection, upstreams: UpstreamRegistry, ...) -> int:
        weather = await WeatherGateway.fetch(upstreams=upstreams, city=city)
```

설정에 없는 이름을 요청하면 **즉시 예외**다. `None`을 돌려주면 호출자가 확인을 잊고, 설정 누락이 엉뚱한 곳에서 `AttributeError`로 나타난다.

### 5.7 기동 시 업스트림을 찔러보지 않는다

lifespan은 DB와 Redis를 확인하지만 **업스트림은 확인하지 않는다.** 남의 서버가 잠깐 죽었다고 우리 배포가 막히면 장애가 전파된다.

같은 이유로 `/health/ready`는 업스트림 상태를 **보고만 하고 판정에 넣지 않는다.**

```json
{ "status": "ok", "checks": {"database": true, "redis": true},
  "upstreams": {"a": true, "b": false} }
```

`b`가 죽었는데 200이다. "우리가 요청을 처리할 수 있는가"와 "연동이 건강한가"는 다른 질문이고, readiness는 앞의 질문에 답한다. 뒤의 질문은 모니터링이 볼 값이다.

검사 대상은 `health_path`를 준 업스트림만이다. 전부 찌르면 우리 readiness 프로브가 남의 서버에 부하를 만든다.

### 5.8 아직 하지 않는 것

- **서킷 브레이커.** 연속 실패 시 호출을 끊어 상대에게 회복 시간을 준다. 타임아웃 + 커넥션 상한 + 재시도 제한으로 최악은 막았고, 브레이커는 상태(실패 카운트)를 워커 간에 공유해야 해서 Redis가 얽힌다. 실제로 필요해지면 추가한다.
- **응답 캐싱.** Redis가 이미 있으니 붙이기는 쉽지만, 무효화 정책이 업스트림마다 다르다.
- **분산 추적.** 요청 ID는 이미 전파한다(§0). OTel 스팬 연결은 Phase 6.

---

## 6. 목표 디렉터리 구조

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
   │  ├─ db/                    #   engine, deps, Table 도구, 행 dataclass, 쿼리 조각
   │  ├─ cache/                 #   redis 헬퍼
   │  ├─ security/              #   token encode/decode, hashing, Principal — 도메인 무지
   │  ├─ http/                  #   업스트림 전송 계층 (§5). 상대가 무슨 서버인지 모른다
   │  ├─ errors/                #   예외 타입 + 에러 코드
   │  ├─ pagination.py
   │  ├─ response.py
   │  └─ observability/         #   otel, prometheus
   ├─ modules/                  # common, core import
   │  ├─ auth/                  #   로그인, 토큰 발급, 세션 저장소
   │  ├─ user/
   │  ├─ rbac/
   │  │  └─ gateway.py          #     외부 서버 어댑터 (§5). 필요한 모듈에만 둔다
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

## 7. 구축 순서

우선순위대로. 각 단계가 다음 단계의 전제다. **Phase 4가 목적지고 1~3은 거기까지 가는 길이다.**

### Phase 1 — 뼈대 (여기서 타협하면 나중에 못 고친다) ✅
- [x] `core/config.py` — pydantic-settings, `.env.example` 동기화(테스트로 강제)
- [x] `bootstrap/lifespan.py` — 엔진·Redis를 여기서만 생성. **전역 인스턴스 0개**
- [x] `common/db/deps.py` — `ConnDep` / `TxDep`
- [x] alembic 초기화 + **첫 리비전 커밋**(`0001_baseline`, 빈 스키마). `create_all` 없음
- [x] `.importlinter` + CI 연결 — 계약 3개(layers, core 독립, common 무지)
- [x] `bootstrap/middleware.py` — CORS(허용 오리진은 설정값), 요청 ID (§0)
- [x] `tests/conftest.py` — testcontainers, 트랜잭션 롤백 격리(`create_savepoint`)
- [x] ruff + pre-commit + CI (lint / test / migrations 3잡)

Phase 1에서 추가로 확정한 것:
- `common/db/base.py` — `METADATA` + **제약 이름 규칙**. 첫 리비전 전에 정해야 하는 값이다
- `common/openapi.py` — operation id 고정 + 중복 시 **기동 실패** (§0의 계약)
- `bootstrap/health.py` — liveness/readiness 분리. liveness가 DB를 보면 순단에 프로세스가 죽는다
- `tests/unit/test_architecture_rules.py` — §8 규칙표의 "코드리뷰/grep" 항목을 **AST 검사**로 승격
  (규칙 #1·#3·#4·#5·#10, `sys.exit` 금지). 지금은 공허하게 통과하고, Phase 3부터 일한다

### Phase 2 — 공용 계층 ✅
- [x] `common/db/schema.py` — 공통 컬럼 팩토리 + `define_table()` (deleted=id 방식)
- [x] `common/db/sql.py` — `alive()` / `select_alive()` / `soft_delete()` / `columns()` 조각
- [x] `common/errors/` — 에러 **코드** 체계, 예외 핸들러, i18n 카탈로그(`locale/{ko,en}.json`)
- [x] `common/response.py` — msgspec 응답, 에러 응답 계약
- [x] `common/security/` — 토큰 encode/decode + argon2 해싱. 도메인 import 0

DB를 SQLite로 바꾸면서 같이 들어간 것 (§1.6):
- [x] `common/db/engine.py` — PRAGMA(FK/WAL/busy_timeout) + 명시적 `BEGIN`. **엔진을 만드는 유일한 함수**
- [x] `common/db/types.py` — `BigIntPK`(방언별 자동증가), `UTCDateTime`(tz 보존)
- [x] alembic `render_as_batch`, `migrations/env.py`도 같은 엔진 팩토리 사용
- [x] 테스트에서 testcontainers 제거 → 임시 SQLite 파일 + `fakeredis`. **Docker 불필요**

ORM을 걷어내고 Core로 내려오면서 같이 들어간 것 (§1.6):
- [x] `common/db/model.py` — `Record` / `SoftDeletable` 행 dataclass 조상
- [x] `common/db/sql.py` — `columns()`가 dataclass 필드에서 SELECT 목록을 뽑는다
- [x] 엔진 팩토리의 방언 분기 — SQLite는 PRAGMA, 서버 DB는 커넥션 풀
- [x] `SUPPORTED_DRIVERS` — 지원한다고 말한 방언과 검증한 방언이 갈라지지 않게
- [x] `tests/unit/test_dialect_portability.py` — 모든 문장을 3개 방언으로 컴파일

Phase 2에서 추가로 확정한 것:
- `common/pagination.py` — §4.3의 `Page{items, next_cursor, has_next}`. Phase 4에서 쓰지만
  화면이 처음부터 의존하는 계약이라(§0) 지금 고정한다. `total`은 넣지 않는다

### Phase 3 — 첫 모듈 (`user`)로 패턴 확정 ✅
- [x] `modules/user/` 5파일 전부 + **테스트 3종(unit/integration/e2e) 동시에**
- [x] 여기서 확정된 모양이 이후 모든 모듈의 템플릿이 된다

**확정된 모듈 템플릿:**

```
modules/<name>/
├─ __init__.py      # router 만 노출. bootstrap 이 보는 유일한 이름
├─ router.py        # HTTP. 읽기는 ConnDep, 쓰기는 TxDep (§1.1)
├─ schema.py        # 요청/응답. UserResponse 처럼 응답은 허용 목록으로 (해시 노출 방지)
├─ service.py       # 규칙. commit 금지, Request 금지, 에러는 코드로
├─ repository.py    # 쿼리만. select_alive() 를 쓰고, commit 은 하지 않는다
├─ model.py         # define_table() + 행 dataclass (Record / SoftDeletable)
└─ deps.py          # 필요할 때만. 요청 스코프 횡단 관심사
```

새 모듈을 만들 때 잊기 쉬운 두 곳 — 둘 다 테스트가 잡는다:
- `bootstrap/models.py` 에 모델 import 추가 (안 하면 autogenerate 가 **빈 리비전**을 만든다)
- `locale/{ko,en}.json` 에 에러 코드 추가 (안 하면 사용자에게 `user.not_found` 날문자가 보인다)

Phase 3에서 추가로 확정한 것:
- `common/security/principal.py` — `Principal(id, is_superuser)` + `can_act_on()`.
  §4.6의 소유권 규칙을 한 곳에 두고, `modules/auth`(발급)와 `modules/user`(소비)가
  서로를 import하지 않게 한다
- `tests/factories.py` — argon2 해시를 프로세스당 한 번만 계산한다. 안 하면 테스트가 느려진다
- `migrations/env.py` 의 `render_item` — 커스텀 타입의 **import까지** 렌더링한다
- **인증은 Phase 5다.** `modules/user/deps.py` 가 지금은 401을 낸다. 가짜 주체를 넣으면
  인가가 걸린 척하는 엔드포인트가 되고, 그게 Phase 5까지 살아남으면 그대로 구멍이다.
  라우트를 지금 노출하는 이유는 §0 — OpenAPI 계약이 확정되어야 화면 작업을 병행할 수 있다

### Phase 4 — 게시판 (§4) ★메인 비즈니스
순서가 곧 의존 방향이다. 각 항목은 테스트와 같은 PR로 간다.

- [x] `board/board/` — 게시판 CRUD, `slug` 조회, `require_board` deps (§4.6)
- [x] `board/post/` — 작성/수정/삭제 + **keyset 목록** (§4.3)
  - [x] 소유권 검사: *타인 글 수정 → 403* (§4.6의 FBA 버그 재발 방지)
  - [x] 커서 페이지네이션: 페이징 중 새 글이 들어와도 중복·누락 없음
  - [x] 고정글은 별도 쿼리로 첫 페이지에만. 커서에 영향을 주지 않는다
- [ ] `board/comment/` — `path` 기반 트리, 깊이 1단 제한 (§4.2)
  - [ ] `comment_count` 갱신이 같은 트랜잭션인지 (§4.4) — 롤백 시 카운트도 롤백
  - [ ] 자식 있는 댓글 삭제 → `is_removed` 묘비, 자식은 계속 보임 (§4.7)
- [ ] `board/post/view_counter.py` — Redis 버퍼 + flush 소비자 (§4.5)
  - [ ] 상세 조회가 여전히 `ConnDep`인지 (쓰기 트랜잭션이 아님)
  - [ ] **Redis 다운 시에도 조회 200** — fake redis로 예외 주입
- [ ] `board/attachment/` — 라우터가 `UploadFile` 처리, 서비스는 원시 타입만 (§4.9)
- [ ] FTS5 가상 테이블 + 동기화 트리거 (§4.8)
- [ ] `comment_count` 정합성 보정 배치 + 고아 첨부 정리 배치

`board` + `post` 까지 오면서 확정한 것:
- **컨텍스트 내부 계약이 린트로 존재한다.** `.importlinter` 의 `board-internal` 이
  `post → board` 방향을 못박는다. `comment`·`attachment` 는 `post` 위에 한 줄 더 붙는다
- **`bump_comment_count` 는 `post` 가 소유한다.** 댓글이 `post` 테이블을 직접 건드리면
  §4.1 의 의존 방향이 역류한다 — 쿼리를 미리 만들어 두고 `comment` 가 부르게 한다
- **인증이 없는 동안의 권한 판정.** `require_board` 는 `read_role == 'anonymous'` 만
  통과시키고 나머지는 401 이다. 역할 계층은 Phase 5 — 주체가 없으니 판정할 것도 없다
- **초안(`draft`)은 404 다.** 작성자 본인에게 보여주려면 주체가 필요하고, 그때까지
  열어두면 남의 초안이 공개된다
- **쓰기 라우트는 본문 검증보다 인증이 먼저다.** 인증 안 된 호출자에게 422 를 주면
  어떤 필드에 어떤 제약이 걸렸는지 알려주는 셈이다

### Phase 5 — 인증/인가
- [ ] `modules/auth/` — 로그인, 리프레시, `UserSessionStore`(캐시 무효화 단일 지점)
- [ ] `modules/rbac/` — 권한 검사를 `Depends`로. §4.6 의 `read_role`/`write_role` 이 여기서 실제로 걸린다
- [ ] 잠긴 계정·잠긴 역할의 즉시 무효화 테스트
- [ ] Phase 4 에서 401 로 막아둔 쓰기 엔드포인트가 실제 주체로 도는지 e2e

**왜 게시판 뒤로 갔나:** 인증은 게시판의 전제가 아니라 **게시판 위에 얹는 것**이다.
읽기(`read_role='anonymous'`)는 주체 없이 성립하고, 쓰기는 `PrincipalDep` 가 401 을 내는
채로 계약(§0)만 먼저 확정해두면 된다 — Phase 3 의 `user` 모듈이 이미 그 모양이다.
반대 순서였다면 인증을 먼저 만들고 그것을 쓸 도메인이 없어서, 무엇이 필요한지 모르는 채로
역할 모델을 정하게 된다.

### Phase 6 — 운영
- [ ] `common/observability/` — OTel, Prometheus
- [ ] `modules/audit/` — 감사 로그 (미들웨어는 큐에 넣기만)
- [ ] compose: `migrate` → `api` 순서 보장
- [ ] CI 커버리지 게이트

---

## 8. 지켜야 할 규칙 요약

| # | 규칙 | 강제 수단 |
|---|---|---|
| 1 | `service`/`repository`에서 `commit()` 금지 | 코드리뷰 + grep 체크 |
| 2 | 의존 방향은 `bootstrap → modules → common → core` | `lint-imports` (CI) |
| 3 | 모듈 최상위에서 I/O 자원 생성 금지 | 리뷰 + import 테스트 |
| 4 | 앱 코드에서 `create_all()` 금지 | `alembic check` (CI) |
| 5 | 서비스 시그니처에 `Request`/`Response` 금지 | 코드리뷰 |
| 6 | 소프트 삭제 조건은 `alive()` 하나뿐 | 유닛 테스트가 AST로 검사 |
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
| 17 | 엔진 생성은 `common/db/engine.py` 하나뿐 | 유닛 테스트가 AST로 검사 |
| 18 | 방언 전용 코드는 `db/engine.py`·`db/types.py` 밖으로 안 나간다 | 유닛 테스트가 AST로 검사 |
| 19 | 시각은 aware UTC. naive 저장은 거부된다 | `UTCDateTime` 이 예외 |
| 20 | 5xx 응답 본문에 내부 정보 금지 | e2e 테스트 (§2.6) |
| 21 | 새 모델은 `bootstrap/models.py` 에 등록한다 | `test_model_registry.py` |
| 22 | soft delete 테이블의 unique 는 `deleted` 를 포함한다 | `test_model_registry.py` |
| 29 | 모든 쿼리가 지원 방언 전부에서 컴파일된다 | `test_dialect_portability.py` |
| 30 | 모델 dataclass 필드와 테이블 컬럼이 일치한다 | `test_model_registry.py` |
| 31 | `String` 에는 항상 길이를 준다 (MySQL 필수) | DDL 컴파일 테스트 |
| 23 | `raise ...(code=)` 의 코드는 카탈로그에 있어야 한다 | `test_errors.py` (AST) |
| 24 | 응답 스키마는 허용 목록. 모델을 그대로 직렬화하지 않는다 | 리뷰 + e2e(해시 미노출) |
| 25 | `httpx.AsyncClient` 생성은 `common/http/registry.py` 하나뿐 | 유닛 테스트가 AST로 검사 |
| 26 | wire DTO는 `gateway.py` 밖으로 나가지 않는다 | 유닛 테스트가 AST로 검사 |
| 27 | POST/PATCH는 재시도하지 않는다 (명시 opt-in만) | `common/http/client.py` + 테스트 |
| 28 | 업스트림 장애가 우리 readiness를 깨뜨리지 않는다 | e2e 테스트 (§5.7) |
| 32 | 모듈 DTO는 `~Request`/`~Response`로 끝난다 | 유닛 테스트가 AST로 검사 |

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
