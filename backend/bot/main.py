"""Telegram-бот: /start, кнопка Mini App, учёт блокировки бота и отправка очереди.

Запуск: python -m bot.main
"""

import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.filters import CommandStart
from aiogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AccountStatus, User
from app.security.telegram_auth import TelegramUser
from app.services import users as users_svc
from app.timeutil import now_utc
from bot.outbox_worker import DeliveryError, run_forever

log = logging.getLogger(__name__)
router = Router()

START_TEXT = {
    AccountStatus.UNREGISTERED: "Здравствуйте! Чтобы зарегистрироваться, откройте приложение.",
    AccountStatus.PENDING: "Ваша заявка на рассмотрении. Мы сообщим о решении.",
    AccountStatus.REJECTED: "Ваша заявка отклонена. Можно подать новую в приложении.",
    AccountStatus.ACTIVE: "Откройте приложение, чтобы продолжить работу.",
}


def _app_keyboard() -> InlineKeyboardMarkup | None:
    url = get_settings().miniapp_url
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=url))]
        ]
    )


@router.message(CommandStart(), F.chat.type == "private")
async def on_start(message: Message) -> None:
    tg = message.from_user
    assert tg is not None
    async with get_sessionmaker()() as session:
        user = await users_svc.upsert_from_telegram(
            session,
            TelegramUser(
                id=tg.id, first_name=tg.first_name, last_name=tg.last_name, username=tg.username
            ),
            started_bot=True,
        )
        await session.commit()
        status = user.status
    text = users_svc.STATUS_MESSAGES.get(status) or START_TEXT[status]
    blocked = status in users_svc.STATUS_MESSAGES
    await message.answer(text, reply_markup=None if blocked else _app_keyboard())


@router.my_chat_member(F.chat.type == "private")
async def on_my_chat_member(event: ChatMemberUpdated) -> None:
    """Пользователь заблокировал или разблокировал бота."""
    blocked = event.new_chat_member.status == "kicked"
    async with get_sessionmaker()() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == event.chat.id).with_for_update()
        )
        if user is None:
            return
        if blocked:
            user.bot_blocked_at = now_utc()
        else:
            user.bot_blocked_at = None
            user.bot_started_at = user.bot_started_at or now_utc()
        await session.commit()


def make_sender(bot: Bot):
    async def send(method: str, chat_id: int, payload: dict[str, Any]) -> int | None:
        if method != "sendMessage":
            raise DeliveryError(f"неизвестный метод {method}", permanent=True)
        markup = payload.get("reply_markup")
        try:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=payload["text"],
                parse_mode=payload.get("parse_mode"),
                reply_markup=InlineKeyboardMarkup.model_validate(markup) if markup else None,
            )
        except TelegramRetryAfter as e:
            raise DeliveryError(str(e), retry_after=e.retry_after) from e
        except TelegramForbiddenError as e:
            # Человек не нажимал /start или заблокировал бота — повтор не поможет.
            raise DeliveryError(
                str(e), permanent=True, bot_blocked="blocked" in e.message.lower()
            ) from e
        except TelegramBadRequest as e:
            raise DeliveryError(str(e), permanent=True) from e
        except (TelegramNetworkError, TelegramServerError) as e:
            raise DeliveryError(str(e)) from e
        return msg.message_id

    return send


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    if settings.miniapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Приложение", web_app=WebAppInfo(url=settings.miniapp_url)
            )
        )

    worker = asyncio.create_task(run_forever(get_sessionmaker(), make_sender(bot)))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "my_chat_member", "chat_member"])
    finally:
        worker.cancel()


if __name__ == "__main__":
    asyncio.run(main())
