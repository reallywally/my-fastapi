"""ASGI 진입점. `create_app()` 호출만 한다 (§5).

uv run uvicorn app.main:app --reload
"""

from app.bootstrap.app import create_app

app = create_app()
