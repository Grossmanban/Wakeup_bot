import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import BOT_TOKEN, OWNER_TG

os.environ["BOT_TOKEN"] = BOT_TOKEN
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://wakeup:wakeup@localhost:5432/wakeup_test"
)
os.environ["OWNER_TELEGRAM_IDS"] = str(OWNER_TG)
os.environ["MINIAPP_URL"] = "https://app.example.com"

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    """Чистая схема из миграций — так тесты проверяют и сами миграции."""
    env = {**os.environ}
    cmd = [sys.executable, "-m", "alembic"]
    subprocess.run(
        [*cmd, "downgrade", "base"], cwd=BACKEND, env=env, check=True, capture_output=True
    )
    subprocess.run([*cmd, "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)


TABLES = (
    "telegram_outbox, audit_events, user_permissions, supervisions, user_networks, "
    "name_change_requests, registration_requests, networks, users"
)


@pytest.fixture(autouse=True)
async def _clean():
    async with get_sessionmaker()() as s:
        await s.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await s.commit()
    yield


@pytest.fixture
async def session():
    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
