"""사용자 모듈. §3.4 의 vertical slice — 한 기능이 한 폴더에 갇힌다.

`bootstrap/router.py` 는 `router` 만 본다. 나머지는 이 폴더 밖으로 나가지 않는다.
"""

from app.modules.user.router import router

__all__ = ['router']
