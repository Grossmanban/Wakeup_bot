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
os.environ["BOT_USERNAME"] = "wakeup_test_bot"
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


class FakeGateway:
    """Подменный Telegram: записывает вызовы, умеет имитировать ошибки."""

    def __init__(self):
        from app.telegram_api import TelegramError

        self.Error = TelegramError
        self.members: dict[tuple[int, int], str] = {}
        self.unknown: set[tuple[int, int]] = set()
        self.rights = {"status": "administrator", "can_invite_users": True, "can_manage_topics": True}
        self.sent: list[tuple[str, int, dict]] = []
        self.fail_send = None
        self.fail_link = None
        self.links = 0
        self.topics: list[tuple[int, str]] = []

    async def get_chat(self, chat_id):
        return {"id": chat_id, "title": f"Группа {chat_id}", "type": "supergroup", "is_forum": True}

    async def member_status(self, chat_id, user_id):
        if (chat_id, user_id) in self.unknown:
            raise self.Error("Bad Request: PARTICIPANT_ID_INVALID", permanent=True)
        return self.members.get((chat_id, user_id), "left")

    async def bot_rights(self, chat_id):
        return dict(self.rights)

    async def create_invite_link(self, chat_id, name, expire_seconds):
        if self.fail_link:
            raise self.fail_link
        self.links += 1
        return f"https://t.me/+invite{self.links}"

    async def create_forum_topic(self, chat_id, name):
        self.topics.append((chat_id, name))
        return 100 + len(self.topics)

    async def send(self, method, chat_id, payload):
        if self.fail_send:
            raise self.fail_send
        self.sent.append((method, chat_id, payload))
        return 1000 + len(self.sent)


@pytest.fixture(autouse=True)
def gateway():
    from app import telegram_api

    gw = FakeGateway()
    telegram_api.set_gateway(gw)
    yield gw
    telegram_api.set_gateway(None)
