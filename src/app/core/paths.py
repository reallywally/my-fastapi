"""경로 상수. import 부작용 없음 (§2.2)."""

from pathlib import Path
from typing import Final

CORE_DIR: Final = Path(__file__).resolve().parent
APP_DIR: Final = CORE_DIR.parent
SRC_DIR: Final = APP_DIR.parent
PROJECT_ROOT: Final = SRC_DIR.parent

MIGRATIONS_DIR: Final = PROJECT_ROOT / 'migrations'
ALEMBIC_INI: Final = PROJECT_ROOT / 'alembic.ini'
