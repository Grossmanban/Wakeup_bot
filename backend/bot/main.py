"""Telegram-бот: /start, кнопка Mini App, учёт блокировки бота и отправка очереди.

Скрытый режим: посторонним (без приглашения или реферальной ссылки) бот ничего не
отвечает и не показывает кнопок, попытка пишется в журнал (app/services/entry.py).

Запуск: python -m bot.main
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonDefault,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy import select

from app import jobs
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AccountStatus, User
from app.security.telegram_auth import TelegramUser
from app.services import broadcasts as broadcasts_svc
from app.services import entry as entry_svc
from app.services import groups as groups_svc
from app.telegram_api import get_gateway
from app.timeutil import now_utc
from bot.outbox_worker import run_forever

log = logging.getLogger(__name__)
router = Router()


def _app_keyboard() -> InlineKeyboardMarkup | None:
    url = get_settings().miniapp_url
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=url))]
        ]
    )


def _tg(u) -> TelegramUser:
    return TelegramUser(
        id=u.id, first_name=u.first_name, last_name=u.last_name, username=u.username
    )


@router.message(CommandStart(), F.chat.type == "private")
async def on_start(message: Message, command: CommandObject) -> None:
    assert message.from_user is not None
    async with get_sessionmaker()() as session:
        reply = await entry_svc.handle_start(session, _tg(message.from_user), command.args or "")
    if reply is None:
        return  # посторонний: никаких сообщений и кнопок
    await message.answer(reply.text, reply_markup=_app_keyboard() if reply.show_app else None)
    url = get_settings().miniapp_url
    if reply.show_app and url and message.bot is not None:
        # Кнопка меню — только в чате человека с доступом, а не у всех.
        await message.bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=url)),
        )


@router.my_chat_member(F.chat.type.in_({"group", "supergroup"}))
async def on_bot_in_group(event: ChatMemberUpdated) -> None:
    """Бота добавили в группу, сделали админом или удалили (ТЗ 15.1)."""
    status = event.new_chat_member.status
    async with get_sessionmaker()() as session:
        await groups_svc.upsert_group(
            session,
            chat_id=event.chat.id,
            title=event.chat.title or "",
            chat_type=event.chat.type,
            is_forum=bool(event.chat.is_forum),
            bot_status=status.value if hasattr(status, "value") else str(status),
        )


@router.chat_member(F.chat.type.in_({"group", "supergroup"}))
async def on_member_change(event: ChatMemberUpdated) -> None:
    """Вступление и выход участников (приходит, если бот — администратор группы)."""
    status = event.new_chat_member.status
    async with get_sessionmaker()() as session:
        await groups_svc.on_member_event(
            session,
            event.chat.id,
            event.new_chat_member.user.id,
            status.value if hasattr(status, "value") else str(status),
        )


@router.callback_query(F.data.startswith("ack:"))
async def on_ack(query: CallbackQuery) -> None:
    """Кнопка «Я ознакомился» под рассылкой."""
    try:
        bid = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.answer()
        return
    async with get_sessionmaker()() as session:
        if not await entry_svc.note_interaction(
            session, _tg(query.from_user), "callback", query.data
        ):
            return  # посторонний: без ответа
        user = await session.scalar(select(User).where(User.telegram_id == query.from_user.id))
        # Отключённый или заблокированный аккаунт не может выполнять рабочие действия (ТЗ 2.3).
        active = user is not None and user.status == AccountStatus.ACTIVE and user.app_access
        ok = active and await broadcasts_svc.acknowledge(session, user, bid)
    await query.answer(
        "Отмечено: вы ознакомились" if ok else "Не удалось отметить", show_alert=not ok
    )


@router.message(F.chat.type == "private", F.reply_to_message)
async def on_reply(message: Message) -> None:
    """Ответ сотрудника на рассылку («Ответить» на её сообщение)."""
    media_kind = file_id = None
    if message.photo:
        media_kind, file_id = "photo", message.photo[-1].file_id
    elif message.document:
        media_kind, file_id = "document", message.document.file_id
    elif message.video:
        media_kind, file_id = "video", message.video.file_id
    elif message.voice:
        media_kind, file_id = "voice", message.voice.file_id
    async with get_sessionmaker()() as session:
        if not await entry_svc.note_interaction(
            session, _tg(message.from_user), "message", message.text or message.caption
        ):
            return
        user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
        ok = (
            user is not None and user.status == AccountStatus.ACTIVE and user.app_access
        ) and await broadcasts_svc.save_reply(
            session,
            user,
            message.reply_to_message.message_id,
            message.text or message.caption,
            media_kind,
            file_id,
        )
    if ok:
        await message.answer("Ответ передан")


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


@router.message(F.chat.type == "private")
async def on_other_message(message: Message) -> None:
    """Любое другое сообщение: посторонних записываем в журнал, ответа нет никому."""
    if message.from_user is None:
        return
    async with get_sessionmaker()() as session:
        await entry_svc.note_interaction(
            session, _tg(message.from_user), "message", message.text or message.caption
        )


@router.callback_query()
async def on_other_callback(query: CallbackQuery) -> None:
    async with get_sessionmaker()() as session:
        await entry_svc.note_interaction(session, _tg(query.from_user), "callback", query.data)
    await query.answer()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    # Общая кнопка меню видна всем, кто откроет бота, — убираем её. У людей с доступом
    # кнопка ставится в их чате при /start.
    await bot.set_chat_menu_button(menu_button=MenuButtonDefault())

    worker = asyncio.create_task(run_forever(get_sessionmaker(), get_gateway().send))
    scheduler = asyncio.create_task(run_jobs_forever())
    try:
        await dp.start_polling(
            bot, allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"]
        )
    finally:
        worker.cancel()
        scheduler.cancel()


async def run_jobs_forever() -> None:
    while True:
        try:
            await jobs.run_due(get_sessionmaker())
        except Exception:
            log.exception("scheduled jobs iteration failed")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
