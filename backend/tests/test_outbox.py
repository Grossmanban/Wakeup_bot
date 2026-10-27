from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import AuditEvent, OutboxStatus, TelegramOutbox, User
from app.services import outbox
from bot.outbox_worker import BACKOFF_SECONDS, DeliveryError, process_due
from tests.helpers import auth


async def _enqueue(session, key="k1", chat_id=777, related_user_id=None):
    await outbox.enqueue(
        session, dedup_key=key, chat_id=chat_id, text="hi", related_user_id=related_user_id
    )
    await session.commit()


async def test_dedup_key_prevents_duplicates(session):
    await _enqueue(session)
    await _enqueue(session)
    rows = (await session.execute(select(TelegramOutbox))).scalars().all()
    assert len(rows) == 1


async def test_sent_records_message_id(session):
    await _enqueue(session)
    sent = []

    async def send(method, chat_id, payload):
        sent.append((method, chat_id, payload["text"]))
        return 555

    assert await process_due(get_sessionmaker(), send) == 1
    assert await process_due(get_sessionmaker(), send) == 0  # повторный запуск ничего не шлёт
    assert sent == [("sendMessage", 777, "hi")]
    msg = await session.scalar(select(TelegramOutbox))
    await session.refresh(msg)
    assert msg.status == OutboxStatus.SENT
    assert msg.tg_message_id == 555


async def test_temporary_error_is_retried_later(session):
    await _enqueue(session)

    async def send(*_):
        raise DeliveryError("timeout")

    assert await process_due(get_sessionmaker(), send) == 1
    msg = await session.scalar(select(TelegramOutbox))
    await session.refresh(msg)
    assert msg.status == OutboxStatus.PENDING
    assert msg.attempts == 1
    assert msg.last_error == "timeout"
    # Следующая попытка — не раньше паузы, поэтому сразу ничего не берётся.
    assert await process_due(get_sessionmaker(), send) == 0
    assert (msg.next_attempt_at - msg.created_at).total_seconds() >= BACKOFF_SECONDS[0] - 1


async def test_permanent_error_fails_and_is_audited(client, session):
    await client.get("/api/me", headers=auth(321))
    user = await session.scalar(select(User).where(User.telegram_id == 321))
    await _enqueue(session, chat_id=321, related_user_id=user.id)

    async def send(*_):
        raise DeliveryError(
            "Forbidden: bot was blocked by the user", permanent=True, bot_blocked=True
        )

    await process_due(get_sessionmaker(), send)
    msg = await session.scalar(select(TelegramOutbox))
    await session.refresh(msg)
    assert msg.status == OutboxStatus.FAILED
    ev = await session.scalar(
        select(AuditEvent).where(AuditEvent.action == "telegram.delivery_failed")
    )
    assert ev.result == "error"
    await session.refresh(user)
    assert user.bot_blocked_at is not None
    assert user.can_message is False
