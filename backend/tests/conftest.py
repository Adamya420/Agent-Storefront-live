"""Load .env for the test process so integration tests see real credentials.

Root cause this fixes (ERRORS.md 2026-09-01): the T0 integration tests read
os.getenv("DATABASE_URL") directly and SKIP when it's empty. Nothing loaded
.env into the pytest process (only alembic/env.py called load_dotenv), so the
five DB-touching tests silently skipped and a green-looking "1 passed, 5 skipped"
hid the fact that nothing was actually verified. Autoloading here makes the
integration suite self-sufficient: it either runs or fails, never silently skips
because credentials were merely unloaded.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the project root
env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(env_path)
