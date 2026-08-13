# 코드 리뷰 — CTO 관점

- 대상: `my-fastapi` @ `5119c32` (main)
- 일자: 2026-08-13
- 범위: 전체 저장소 (src 1,588 statements / tests 351 케이스)
- 검증 방법: 전체 테스트·커버리지 실행, 프로덕션 설정 재현, 요청당 커넥션 계측

---

## 0. 총평

**이 저장소의 가장 큰 자산은 코드가 아니라 "규칙이 기계로 존재한다"는 사실이다.**
의존 방향은 `lint-imports`가, 소프트 삭제 조건·엔진 생성 지점·DTO 명명·방언 누수는
`test_architecture_rules.py`가 AST로 막는다. 351개 테스트가 48초에 돌고 커버리지 92%,
Docker 없이 어디서나 실행된다. 이 단계의 사내 프로젝트에서 흔치 않은 수준이다.

동시에 **지금 상태로는 배포할 수 없다.** 이유는 설계가 아니라 운영 공백이다:
서명키 가드에 구멍이 있고, 인증 없이 전체 사용자 이메일이 나가며, **로깅이 아예 구성되어
있지 않다.** 세 번째가 특히 무겁다 — 장애가 나면 조사할 수단이 없다.

| 축 | 평가 | 근거 |
|---|---|---|
| 아키텍처 일관성 | **상** | 4단 계층 + CI 강제. 규칙과 코드가 어긋나지 않는다 |
| 테스트 | **상** | 3종 분리, 롤백 격리, 92% |
| 도메인 완성도 | **중** | board/post/comment 완료. 인증(Ph5)·운영(Ph6) 미착수 |
| 보안 | **하** | P0 2건. 인가 계층이 아직 없다는 것과는 별개의 문제 |
| 운영 준비도 | **하** | 로깅·Dockerfile·배포 파이프라인·레이트리밋 전무 |
| 문서 | **상** (과한 쪽) | 설계문서 1,247줄. 아래 §5 참조 |

**결론: 기술적 기반은 계속 간다. Phase 5로 넘어가기 전에 §1의 P0 3건을 먼저 닫는다.**

---

## 1. P0 — 배포 전 반드시 (3건)

### P0-1. `.env.example`의 JWT 시크릿이 프로덕션 가드를 통과한다 🔴

`config.py`는 운영에서 기본 시크릿을 막는다. 그런데 `.env.example`이 주는 값은
그 상수와 **다르다.**

```
src/app/core/config.py:31   INSECURE_JWT_SECRET = 'change-me-in-production-0000000000'
.env.example:39             JWT_SECRET=change-me-in-production          ← 뒤 11자가 없다
```

`cp .env.example .env` → `ENVIRONMENT=production`으로 재현했다:

```
production 기동 성공: True | secret = change-me-in-production
```

가드가 걸리지 않는다. **공개 저장소에 적힌 문자열이 그대로 운영 서명키가 된다.**
가드가 있다는 사실 자체가 "확인했다"는 착각을 만들기 때문에 더 위험하다.

부가로, 시크릿 **길이 검증이 없다.** `JWT_SECRET=a` 도 통과한다. HS256에서 32바이트
미만 키는 RFC 7518 §3.2가 경고하는 값이다.

**조치**
1. `.env.example`의 값을 `INSECURE_JWT_SECRET`과 **문자 그대로** 일치시킨다.
2. 둘이 갈라지지 않게 `tests/unit/test_env_example_sync.py`에 값 일치 검사를 추가한다
   (지금은 키 존재만 본다).
3. 운영에서 `len(secret) < 32` 를 거부한다. 그리고 "알려진 나쁜 값" 목록으로 확장한다 —
   `secret`, `changeme`, `test` 등.

### P0-2. 인증 없이 전체 사용자 이메일이 나간다 🔴

```
src/app/modules/user/router.py:27   GET /api/v1/users        → PrincipalDep 없음
src/app/modules/user/router.py:32   GET /api/v1/users/{pk}   → PrincipalDep 없음
src/app/modules/user/schema.py:45   UserResponse.email: EmailStr
```

쓰기 경로는 `PrincipalDep`이 401로 막았지만 **읽기 두 개는 열려 있다.** 커서
페이지네이션이 붙어 있어서 전체 사용자 목록을 이메일과 함께 순차로 긁을 수 있다.
`is_superuser`는 응답에서 빠졌지만 계정 열거는 그대로 가능하다.

`README`가 "쓰기 엔드포인트는 Phase 5까지 전부 401"이라고 안전 쪽으로 틀렸다고
적어둔 것과 대비된다 — 읽기 쪽에서는 그 판단이 적용되지 않았다.

**조치** (택1, 권장은 1)
1. `UserResponse`에서 `email`을 뺀다. 공개 프로필에 이메일이 필요한 화면은 없다.
   본인 조회용 `/users/me`를 Phase 5에서 별도로 낸다.
2. 두 라우트에 `PrincipalDep`을 붙여 Phase 5까지 401로 닫는다.

### P0-3. 로깅이 구성되어 있지 않다 🔴

```
$ grep -rn "basicConfig|dictConfig" src/
(없음)
src/app/core/config.py:49    log_level: str = 'INFO'      ← 아무도 읽지 않는 죽은 설정
src/app/common/middleware.py:15  request_id_ctx = ContextVar(...)
                                 # 주석: "로거 필터가 이 값을 붙인다" → 그 필터가 없다
```

결과:
- `settings.log_level`은 어디에도 반영되지 않는다.
- `logger.info(...)`는 uvicorn 기본 설정에 얹혀 사라지거나 형식이 제각각이다.
- **요청 ID가 로그에 붙지 않는다.** §0이 `X-Request-ID`를 계약으로 잡고, 에러 응답
  본문에까지 넣어놓고("사용자가 캡처해서 보내면 로그를 바로 찾을 수 있다"),
  정작 로그에는 그 값이 없다. 계약의 절반만 구현된 상태다.
- 구조화 로그(JSON)가 아니라 운영에서 수집·검색이 어렵다.

**조치**: `bootstrap/logging.py`를 추가한다. `create_app()` 초입에서 `dictConfig`로
1) 레벨을 `settings.log_level`에서 받고, 2) `request_id_ctx`를 읽는 `logging.Filter`를
달고, 3) 비-local 환경에서는 JSON 포매터를 쓴다. Phase 6의 OTel보다 **먼저** 필요하다 —
계측 이전에 로그가 있어야 한다.

---

## 2. P1 — Phase 5 착수 전 (6건)

### P1-1. 쓰기 요청 하나가 커넥션 2개·트랜잭션 2개를 쓴다 (그리고 테스트가 이를 못 잡는다)

`create_post`는 `TxDep`(쓰기)과 `BoardWriteDep`을 같이 선언한다. 그런데
`require_board`의 내부 의존성은 `ConnDep`(읽기)다.

```
src/app/modules/board/post/router.py:36   async def create_post(db: TxDep, board: BoardWriteDep, ...)
src/app/modules/board/board/deps.py:37    async def _dep(db: ConnDep, slug: str) -> Board
```

`get_db`와 `get_db_tx`는 서로 다른 호출자라 FastAPI 캐시가 공유되지 않는다.
프로덕션과 같은 `db_source = engine.connect`로 계측했다:

```
GET  /boards/free/posts  (ConnDep + BoardReadDep)  → 커넥션 1개
POST /boards/free/posts  (TxDep  + BoardWriteDep)  → 커넥션 2개 (서로 다름)
```

세 가지가 따라온다.

1. **권한 판정과 쓰기가 다른 트랜잭션이다.** 게시판을 읽어 통과시킨 뒤, 다른 연결에서
   글을 쓴다. 그 사이의 변경은 보이지 않는다. §1.1이 "한 요청이 일관된 스냅샷을 본다"고
   한 보장이 쓰기 경로에서는 성립하지 않는다.
2. **서버 DB로 옮기면 풀이 절반이 된다.** `pool_size=10`은 동시 쓰기 5건이 된다.
   §1.6이 약속한 방언 교체 시점에 바로 드러난다.
3. **테스트가 이 위상을 재현하지 못한다.** `conftest.py`의 `_pinned`는 두 의존성 모두에게
   **같은 연결 하나**를 준다. 즉 테스트는 커넥션 1개 위상으로, 운영은 2개 위상으로 돈다 —
   여기서 생기는 버그는 CI를 통과한다.

**조치**: `require_board`가 연결을 직접 받지 않게 한다. 라우터가 이미 손에 든 `db`를
넘기거나(`Depends`가 아니라 서비스 호출로 내리기), `deps.py`가 `ConnDep`/`TxDep` 중
호출 문맥에 맞는 것을 받도록 두 벌로 나눈다. 어느 쪽이든 **테스트 픽스처가 운영과 같은
커넥션 위상을 재현하도록** 같이 고친다 — 그러지 않으면 고쳤는지 알 수 없다.

### P1-2. `allow_comment` / `allow_attachment`가 강제되지 않는다

컬럼이 있고, 생성·수정 API로 값을 바꿀 수 있고, 응답에도 나간다. **읽는 코드가 없다.**

```
$ grep -rn "allow_comment" src/ | grep -v "model.py|schema.py|repository.py|board/service.py"
(없음)
```

`allow_comment=False`인 게시판에도 댓글이 그대로 달린다. 관리자가 설정을 껐는데 동작이
바뀌지 않는 것은 UI 버그가 아니라 신뢰 문제다.

**조치**: `comment_service.create`에서 `board.allow_comment`를 확인하고
`BadRequestError(code='comment.not_allowed')`. `attachment`는 Phase 4 잔여 항목이라
그 슬라이스와 같이 간다.

### P1-3. 댓글 작성이 읽기 권한만 확인한다

§4.10 표는 댓글 작성을 `[write_role]`로 적어뒀다. 구현은 읽기만 본다.

```
src/app/modules/board/comment/service.py:46   post = await cls._readable_post(...)
                                              # → board_service.readable() = read 판정
```

지금은 `PrincipalDep`의 401에 가려 보이지 않는다. **Phase 5에서 주체가 생기는 순간
"읽기만 가능한 게시판에 누구나 댓글을 쓸 수 있는" 상태가 된다.** 401이 걷히는 날 함께
열리는 구멍이라 지금 닫는 편이 싸다.

**조치**: `board_service`에 `assert_writable(board, actor)`를 만들고 댓글·글 작성이
같은 함수를 부르게 한다 — §4.1이 "읽기 규칙을 board 슬라이스가 소유한다"고 정한 것과
같은 이유로, 쓰기 규칙도 한 곳에 있어야 한다.

### P1-4. 고정글을 만들 방법이 없다

`is_pinned`는 컬럼·모델·응답·`list_pinned` 쿼리까지 전부 있는데, **API로 설정할 수단이
없다.** `CreatePostRequest`에 없고 `UPDATABLE` 집합에도 없다.

```
src/app/modules/board/post/repository.py:19   UPDATABLE = frozenset({'title', 'content', 'status'})
```

`list_pinned` 경로는 테스트 팩토리로만 도달 가능하다 — 즉 프로덕션에서는 죽은 코드고,
테스트만 그것을 살아 있게 보이게 한다.

**조치**: 관리자 전용 `PATCH /posts/{id}/pin`을 낸다 (권한이 소유권이 아니라 역할이라
일반 수정과 분리하는 것이 맞다). Phase 5까지 미룰 거면 `list_pinned` 호출을 주석이 아니라
**티켓으로** 남긴다.

### P1-5. 커버리지 게이트가 22포인트 놀고 있다

```
pyproject.toml:112   fail_under = 70        실제: 91.83%
```

§2.8이 "숫자는 낮게 시작해도 되지만 *내려가는 것*은 막는다"고 적었는데, 지금 게이트는
92 → 71 하락을 통과시킨다. 규칙이 사실상 꺼져 있다.

**조치**: `fail_under = 90`으로 올린다. 이 저장소는 규칙을 기계로 강제하는 것이 정체성인데
여기만 예외일 이유가 없다.

### P1-6. `/health/ready`가 인증 없이 업스트림을 n개 호출한다

```
src/app/bootstrap/health.py:59   for name in registry.names(): await client.request('GET', health_path)
```

- 순차 호출이다. `health_path`를 준 업스트림이 3개고 각각 느리면 프로브가
  `3 × (connect 2s + read 5s)`까지 늘어진다 → **k8s 프로브 타임아웃으로 멀쩡한 파드가
  not-ready가 된다.** liveness/readiness를 나눈 §의 의도와 반대 결과다.
- 인증이 없다. 외부에서 이 경로를 반복 호출하면 우리 서버를 경유해 업스트림에 부하를
  만들 수 있다 (증폭).

**조치**: `asyncio.gather` + 전체 상한 타임아웃(예: 1초)으로 감싸고, 결과를 짧게
캐시한다(2~5초). 프로브 경로는 인그레스에서 외부 노출을 막는다.

---

## 3. P2 — 운영 준비 (Phase 6 전에 티켓화)

| # | 항목 | 왜 |
|---|---|---|
| 1 | **Dockerfile·배포 파이프라인 없음** | 지금은 "내 노트북에서 uvicorn"이 유일한 실행 방법이다. compose에는 redis만 있다. §2.3이 요구한 `migrate → api` 순서도 아직 없다 |
| 2 | **의존성/시크릿 스캔 없음** | CI에 `pip-audit`(또는 `uv audit`)·secret scanning이 없다. §3.1에서 공급망 위험을 이유로 런타임 플러그인을 버렸는데, 정작 빌드타임 공급망은 검사하지 않는다 |
| 3 | **레이트리밋 없음** | 가입은 이미 열려 있고 argon2는 의도적으로 비싸다. 가입 엔드포인트가 곧 CPU 소진 경로다. Phase 5의 로그인이 붙으면 더 급해진다 |
| 4 | **조회수 버퍼(§4.5) 미구현** | 설계가 스스로 "SQLite에서는 선택이 아니라 필수"라고 적어둔 항목이다. 상세 조회에 조회수가 붙는 순간 쓰기가 직렬화된다 |
| 5 | **목록 응답에 작성자 이름이 없다** | `PostSummaryResponse`는 `author_id`만 준다. 화면은 20개 글마다 `/users/{id}`를 부르게 된다 — DB N+1을 피하려고 만든 구조가 네트워크 N+1을 만든다. §4.1이 예고한 "repository가 조인"을 지금 하는 게 맞다 |
| 6 | **규칙 #11·#12·#13·#14·#24가 "리뷰"로만 강제** | 이 저장소는 기계 강제가 강점인데 이 다섯은 사람이 본다. `#12`(읽기에 TxDep 금지)와 `#24`(응답 허용목록)는 AST로 충분히 잡힌다 — 이미 있는 테스트 패턴에 얹으면 된다 |
| 7 | **`native_enum=False`인데 CHECK 제약이 없다** | 마이그레이션에 `CheckConstraint`가 렌더링되지 않는다 (SQLAlchemy 2.0 기본 `create_constraint=False`). `model.py` 주석의 "VARCHAR + CHECK로 통일"과 실제가 다르다. 앱 경유 쓰기는 Enum 타입이 막으므로 심각도는 낮지만, **문서가 사실과 다른 것 자체**가 이 저장소의 기준에서 결함이다 |
| 8 | **staging에 프로덕션 가드가 걸리지 않는다** | `_guard_production`은 `production`에서만 돈다. staging은 기본 시크릿·`db_echo`·TLS 미검증이 전부 허용된다. staging은 보통 실데이터의 사본을 쥔다 |

---

## 4. 유지할 것 (건드리지 말 것)

리팩터링 압력이 들어와도 아래는 그대로 간다. 전부 **비용을 이미 지불했고 이득이
검증된** 항목이다.

1. **트랜잭션 경계를 DI로** (§1.1). `commit()` 호출이 서비스·레포에 0건이고, AST 테스트가
   그 상태를 유지시킨다.
2. **`alive()` 단일 조건** (§2.4). FBA가 106곳 하드코딩 / 14곳 누락으로 실패한 자리를
   조각 하나로 대체했다. 규칙을 독스트링이 아니라 비교식으로 검사하는 판단도 옳다.
3. **`deleted = 자기 id`** (§1.4). unique 재사용 문제를 마이그레이션 없이 푼 방식.
4. **wire DTO 격리** (§5.5) + `test_gateway_pattern.py`. 문서가 아니라 도는 코드로
   템플릿을 둔 것이 특히 좋다.
5. **테스트 격리를 롤백으로** (§2.8). truncate보다 빠르고, Docker 의존이 없다.
6. **`bootstrap`을 별도 계층으로.** import만으로 연결이 열리지 않는다는 성질이
   유닛테스트 48초를 만든 원인이다.

---

## 5. 조직 관점 한 가지

설계문서 1,247줄, 규칙 32개, 아키텍처 테스트 284줄에 대해 **현재 비즈니스 기능은
게시판 CRUD 하나**다. 규칙 자체는 자산이지만, 두 가지를 의식적으로 관리해야 한다.

- **온보딩 비용.** 새 인원이 첫 PR을 내려면 §1~§8을 읽어야 한다. `README`의 "새 모듈
  추가하기"가 그 완충 역할을 하는데, 실제로 그것만 보고 모듈 하나를 만들 수 있는지
  다음 슬라이스(`attachment`)에서 **한 명에게 문서만 주고 시켜보는 것**을 권한다.
  거기서 막히는 지점이 곧 문서의 실제 결함이다.
- **규칙과 코드의 드리프트.** 이번 리뷰에서 나온 P1-3(§4.10 표 vs 구현),
  P2-7(`CHECK` 주석 vs 마이그레이션)이 이미 그 신호다. 문서가 길수록 어긋난 문장이
  "확인했다"는 잘못된 신호를 준다. **규칙표(§8)의 "리뷰" 항목을 계속 기계 강제로
  옮기는 것**이 이 저장소의 방향이고, P2-6은 그 다음 배치다.

한 문장으로: **설계는 과하지 않다. 다만 문서에 적힌 것 중 아직 코드가 아닌 것들이
어느 것인지 목록으로 유지해야 한다.**

---

## 6. 권고 순서

```
1주차   P0-3 로깅 구성        ← 나머지를 조사 가능하게 만드는 전제
        P0-1 JWT 시크릿 가드
        P0-2 /users 노출 차단
2주차   P1-1 커넥션 위상 (테스트 픽스처 포함)
        P1-2·P1-3 게시판 권한/설정 강제
        P1-5 커버리지 게이트 90
3주차~  P1-4 고정글 · P1-6 readiness
        P2-1 Dockerfile + migrate→api
        → Phase 5 (인증/인가) 착수
```

**Phase 5 착수 조건: P0 3건 완료 + P1-3 완료.** 인가 계층은 그 아래가 맞아 있을 때만
의미가 있고, P1-3은 인증이 붙는 순간 열리는 구멍이라 그 전에 닫아야 한다.
