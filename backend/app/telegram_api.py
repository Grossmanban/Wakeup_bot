"""Шлюз к Telegram Bot API: всё, что системе нужно от Telegram, в одном месте.

Реальная реализация — на aiogram; в тестах подменяется (set_gateway). Ошибки Telegram
приводятся к TelegramError с признаком «окончательная» и паузой для повтора — чтобы
вызывающий код показывал реальный статус, а не имитировал успех (ТЗ 0.1 п.12).
"""

from pathlib import Path
from typing import Any, Protocol


class TelegramError(Exception):
    def __init__(
        self,
        message: str,
        *,
        permanent: bool = False,
        retry_after: float | None = None,
        bot_blocked: bool = False,
    ):
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after
        self.bot_blocked = bot_blocked


class Gateway(Protocol):
    async def get_chat(self, chat_id: int) -> dict[str, Any]: ...
    async def member_status(self, chat_id: int, user_id: int) -> str: ...
    async def bot_rights(self, chat_id: int) -> dict[str, Any]: ...
    async def create_invite_link(self, chat_id: int, name: str, expire_seconds: int) -> str: ...
    async def create_forum_topic(self, chat_id: int, name: str) -> int: ...
    async def send(self, method: str, chat_id: int, payload: dict[str, Any]) -> int | None: ...


class AiogramGateway:
    def __init__(self, token: str):
        from aiogram import Bot

        self.bot = Bot(token)

    async def _call(self, coro):
        from aiogram.exceptions import (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramNetworkError,
            TelegramRetryAfter,
            TelegramServerError,
        )

        try:
            return await coro
        except TelegramRetryAfter as e:
            raise TelegramError(str(e), retry_after=e.retry_after) from e
        except TelegramForbiddenError as e:
            raise TelegramError(
                str(e), permanent=True, bot_blocked="blocked" in e.message.lower()
            ) from e
        except TelegramBadRequest as e:
            raise TelegramError(str(e), permanent=True) from e
        except (TelegramNetworkError, TelegramServerError) as e:
            raise TelegramError(str(e)) from e

    async def get_chat(self, chat_id: int) -> dict[str, Any]:
        chat = await self._call(self.bot.get_chat(chat_id))
        return {
            "id": chat.id,
            "title": chat.title,
            "type": chat.type,
            "is_forum": bool(chat.is_forum),
        }

    async def member_status(self, chat_id: int, user_id: int) -> str:
        m = await self._call(self.bot.get_chat_member(chat_id, user_id))
        status = m.status.value if hasattr(m.status, "value") else str(m.status)
        if status == "restricted" and not getattr(m, "is_member", True):
            return "left"
        return status

    async def bot_rights(self, chat_id: int) -> dict[str, Any]:
        me = await self._call(self.bot.get_me())
        m = await self._call(self.bot.get_chat_member(chat_id, me.id))
        status = m.status.value if hasattr(m.status, "value") else str(m.status)
        return {
            "status": status,
            "can_invite_users": bool(getattr(m, "can_invite_users", False)) or status == "creator",
            "can_manage_topics": bool(getattr(m, "can_manage_topics", False)),
            "can_post_messages": status in ("administrator", "creator", "member"),
            "can_restrict_members": bool(getattr(m, "can_restrict_members", False)),
        }

    async def create_invite_link(self, chat_id: int, name: str, expire_seconds: int) -> str:
        import time

        link = await self._call(
            self.bot.create_chat_invite_link(
                chat_id,
                name=name[:32],
                expire_date=int(time.time()) + expire_seconds,
                member_limit=1,
            )
        )
        return link.invite_link

    async def create_forum_topic(self, chat_id: int, name: str) -> int:
        topic = await self._call(self.bot.create_forum_topic(chat_id, name=name[:128]))
        return topic.message_thread_id

    async def send(self, method: str, chat_id: int, payload: dict[str, Any]) -> int | None:
        from aiogram.types import (
            FSInputFile,
            InlineKeyboardMarkup,
            InputMediaPhoto,
            ReplyParameters,
        )

        markup = payload.get("reply_markup")
        common: dict[str, Any] = {
            "chat_id": chat_id,
            "reply_markup": InlineKeyboardMarkup.model_validate(markup) if markup else None,
        }
        if payload.get("message_thread_id"):
            common["message_thread_id"] = payload["message_thread_id"]
        if payload.get("reply_to_message_id"):
            common["reply_parameters"] = ReplyParameters(
                message_id=payload["reply_to_message_id"], allow_sending_without_reply=True
            )
        if method == "sendMessage":
            msg = await self._call(
                self.bot.send_message(
                    text=payload["text"], parse_mode=payload.get("parse_mode"), **common
                )
            )
            return msg.message_id
        if method == "sendPhoto":
            path = Path(payload["photo_path"])
            await _require_files([path])
            msg = await self._call(
                self.bot.send_photo(
                    photo=FSInputFile(path),
                    caption=payload.get("caption"),
                    parse_mode=payload.get("parse_mode"),
                    **common,
                )
            )
            return msg.message_id
        if method == "sendMediaGroup":
            await _require_files([Path(p) for p in payload["photo_paths"]])
            media = [
                InputMediaPhoto(
                    media=FSInputFile(Path(p)),
                    caption=payload.get("caption") if i == 0 else None,
                    parse_mode=payload.get("parse_mode") if i == 0 else None,
                )
                for i, p in enumerate(payload["photo_paths"])
            ]
            common.pop("reply_markup", None)
            msgs = await self._call(self.bot.send_media_group(media=media, **common))
            return msgs[0].message_id
        raise TelegramError(f"Неизвестный метод {method}", permanent=True)


async def _require_files(paths: list[Path]) -> None:
    import asyncio

    for p in paths:
        if not await asyncio.to_thread(p.exists):
            raise TelegramError(f"Файл фото не найден: {p.name}", permanent=True)


_gateway: Gateway | None = None


def get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        from app.config import get_settings

        _gateway = AiogramGateway(get_settings().bot_token)
    return _gateway


def set_gateway(gw: Gateway | None) -> None:
    global _gateway
    _gateway = gw
