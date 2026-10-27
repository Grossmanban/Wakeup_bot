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
