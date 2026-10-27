"""Фоновая отправка сообщений из telegram_outbox.

Каждое сообщение обрабатывается в отдельной транзакции под блокировкой строки
(FOR UPDATE SKIP LOCKED), поэтому несколько обработчиков не отправят его дважды.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import OutboxStatus, TelegramOutbox, User
from app.services import audit
from app.services.audit import Category
from app.telegram_api import TelegramError
from app.timeutil import now_utc

log = logging.getLogger(__name__)

# Паузы между повторами временных ошибок; после последней — окончательная ошибка.
BACKOFF_SECONDS = (10, 30, 60, 300, 900, 3600, 3 * 3600)


# Ошибка доставки — та же, что у шлюза Telegram.
DeliveryError = TelegramError

# (method, chat_id, payload) -> message_id
Sender = Callable[[str, int, dict[str, Any]], Awaitable[int | None]]

# Обработчики результата по типу объекта: (session, ref_id, ok, message_id, error) -> None
ResultHook = Callable[[AsyncSession, int, bool, int | None, str | None], Awaitable[None]]
RESULT_HOOKS: dict[str, ResultHook] = {}


async def _process_one(session: AsyncSession, send: Sender) -> bool:
    msg = await session.scalar(
        select(TelegramOutbox)
        .where(
            TelegramOutbox.status == OutboxStatus.PENDING,
            TelegramOutbox.next_attempt_at <= now_utc(),
        )
        .order_by(TelegramOutbox.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if msg is None:
        return False

    msg.attempts += 1
    try:
        message_id = await send(msg.method, msg.chat_id, msg.payload)
    except DeliveryError as e:
        msg.last_error = str(e)[:1000]
        if e.permanent or msg.attempts > len(BACKOFF_SECONDS):
            msg.status = OutboxStatus.FAILED
            audit.record(
                session,
                category=Category.SYSTEM,
                action="telegram.delivery_failed",
                actor_id=None,
                target_user_id=msg.related_user_id,
                object_type="telegram_outbox",
                object_id=msg.id,
                reason=msg.last_error,
                result="error",
            )
            if e.bot_blocked and msg.related_user_id:
                user = await session.get(User, msg.related_user_id)
                if user is not None and user.telegram_id == msg.chat_id:
                    user.bot_blocked_at = now_utc()
            await _hook(session, msg, False, None, msg.last_error)
        else:
            delay = e.retry_after or BACKOFF_SECONDS[msg.attempts - 1]
            msg.next_attempt_at = now_utc() + timedelta(seconds=delay)
    else:
        msg.status = OutboxStatus.SENT
        msg.sent_at = now_utc()
        msg.tg_message_id = message_id
        msg.last_error = None
        await _hook(session, msg, True, message_id, None)
    await session.commit()
    return True


async def _hook(session: AsyncSession, msg: TelegramOutbox, ok: bool, message_id, error) -> None:
    if msg.ref_type and msg.ref_id is not None and (hook := RESULT_HOOKS.get(msg.ref_type)):
        await hook(session, msg.ref_id, ok, message_id, error)


async def process_due(
    sessionmaker: async_sessionmaker[AsyncSession], send: Sender, limit: int = 50
) -> int:
    done = 0
    while done < limit:
        async with sessionmaker() as session:
            if not await _process_one(session, send):
                break
        done += 1
    return done


async def run_forever(
    sessionmaker: async_sessionmaker[AsyncSession], send: Sender, idle_seconds: float = 2.0
) -> None:
    while True:
        try:
            n = await process_due(sessionmaker, send)
        except Exception:
            log.exception("outbox worker iteration failed")
            n = 0
        if n == 0:
            await asyncio.sleep(idle_seconds)


def _register_hooks() -> None:
    from app.services import groups, photoreports

    RESULT_HOOKS["invite"] = groups.on_invite_result
    RESULT_HOOKS["photo_delivery"] = photoreports.on_delivery_result
    RESULT_HOOKS["day_post"] = photoreports.on_post_result


_register_hooks()
