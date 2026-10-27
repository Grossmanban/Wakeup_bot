"""Постановка сообщений в очередь Telegram. Отправляет bot/outbox_worker.py."""

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import TelegramOutbox


async def enqueue(
    session: AsyncSession,
    *,
    dedup_key: str,
    chat_id: int,
    text: str,
    related_user_id: int | None = None,
    open_app_button: str | None = None,
) -> None:
    payload: dict[str, Any] = {"text": text, "parse_mode": "HTML"}
    url = get_settings().miniapp_url
    if open_app_button and url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": open_app_button, "web_app": {"url": url}}]]
        }
    stmt = (
        insert(TelegramOutbox)
        .values(
            dedup_key=dedup_key,
            chat_id=chat_id,
            method="sendMessage",
            payload=payload,
            related_user_id=related_user_id,
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )
    await session.execute(stmt)


async def enqueue_raw(
    session: AsyncSession,
    *,
    dedup_key: str,
    chat_id: int,
    method: str,
    payload: dict[str, Any],
    related_user_id: int | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
) -> bool:
    """Поставить сообщение любого вида. Вернёт False, если такое уже стоит в очереди."""
    stmt = (
        insert(TelegramOutbox)
        .values(
            dedup_key=dedup_key,
            chat_id=chat_id,
            method=method,
            payload=payload,
            related_user_id=related_user_id,
            ref_type=ref_type,
            ref_id=ref_id,
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )
    return bool((await session.execute(stmt)).rowcount)
