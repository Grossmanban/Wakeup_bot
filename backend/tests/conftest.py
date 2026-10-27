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
os.environ["MEDIA_DIR"] = os.environ.get("TEST_MEDIA_DIR", "/tmp/wakeup-test-media")

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
    for step in (["downgrade", "base"], ["upgrade", "head"]):
        r = subprocess.run([*cmd, *step], cwd=BACKEND, env=env, capture_output=True, text=True)
        assert r.returncode == 0, f"alembic {' '.join(step)} failed:\n{r.stderr[-3000:]}"


def _tables() -> str:
    from app.models import Base

    return ", ".join(t.name for t in Base.metadata.sorted_tables)


@pytest.fixture(autouse=True)
async def _clean():
    async with get_sessionmaker()() as s:
        await s.execute(text(f"TRUNCATE {_tables()} RESTART IDENTITY CASCADE"))
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


class FakeOcr:
    """Подменный OCR: возвращает заранее заданный текст или бросает исключение."""

    def __init__(self):
        self.texts: list[str | Exception] = []

    async def recognize(self, path):
        assert path.exists()
        item = self.texts.pop(0) if self.texts else ""
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def fake_ocr():
    from app import ocr

    engine = FakeOcr()
    ocr.set_engine(engine)
    yield engine
    ocr.set_engine(None)
