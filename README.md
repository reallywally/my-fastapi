# my-fastapi

게시판 API 서버. 설계 기준은 [ARCHITECTURE.md](./ARCHITECTURE.md) — 코드보다 그쪽이 먼저다.

현재 상태: **Phase 1~2 (뼈대 + 공용 계층) 완료.** 도메인 모듈은 아직 없다 (§6 구축 순서).

DB는 **SQLite**다 (§1.6). 띄울 서버가 없고 `var/app.db` 파일 하나가 전부다.

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
- **트랜잭션은 엔드포인트가 결정한다.** 읽기는 `SessionDep`, 쓰기는 `TxDep` (§1.1).
  service/repository 는 `commit()` 하지 않는다.
- **soft delete 조건을 손으로 쓰지 않는다.** 전역 ORM 필터가 붙인다 (§2.4).
  삭제분을 보려면 `.execution_options(include_deleted=True)`.
- **에러는 메시지가 아니라 코드로 raise 한다.** `raise NotFoundError(code='post.not_found')`.
  문구는 `src/app/locale/{ko,en}.json` 이 갖는다 (§2.6).
- **시각은 항상 aware UTC.** naive 를 저장하려 하면 `UTCDateTime` 이 거부한다.
- **테스트 격리는 롤백이다.** truncate 하지 않는다 (§2.8).
- 화면은 이 저장소에 없다. JSON API 서버다 (§0).
