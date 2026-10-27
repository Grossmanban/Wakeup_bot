"""Рассылки: аудитория, предпросмотр, подтверждение, статусы, «Я ознакомился», ответы (ТЗ 13)."""

import asyncio
from datetime import timedelta
from html import escape
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, timeutil
from app.errors import AppError, Conflict, NotFound
from app.models import (
    ACTIVE_SHIFT,
    AccountStatus,
    Broadcast,
    BroadcastRecipient,
    BroadcastReply,
    DayOff,
    Shift,
    ShiftStatus,
    User,
    UserNetwork,
)
from app.permissions import Perm
from app.services import audit, outbox
from app.services.access import ALL, Actor, scope_for, team_of
from app.services.audit import Category

ALLOWED_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
    "blockquote",
    "a",
    "tg-spoiler",
}
MAX_TEXT = 4096
MAX_CAPTION = 1024


class _Sanitizer(HTMLParser):
    """Оставляет только теги, которые понимает Telegram; остальное экранирует."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href.lower().startswith(("http://", "https://", "tg://")):
                return
            self.out.append(f'<a href="{escape(href, quote=True)}">')
        else:
            self.out.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize(text: str) -> str:
    p = _Sanitizer()
    p.feed(text)
    p.close()
    return p.result().strip()


def plain_length(html: str) -> int:
    class P(HTMLParser):
        n = 0

        def handle_data(self, d):
            self.n += len(d)

    p = P()
    p.feed(html)
    return p.n


async def resolve_audience(session: AsyncSession, actor: Actor, aud: dict[str, Any]) -> list[User]:
    """Получатели: пересечение всех заданных фильтров в пределах области отправителя."""
    filters = {k: v for k, v in aud.items() if v not in (None, [], False) and k != "all"}
    if not filters and not aud.get("all"):
        raise AppError("no_audience", "Выберите аудиторию")
    scope = await scope_for(session, actor, Perm.BROADCASTS_SEND)
    q = select(User).where(User.status == AccountStatus.ACTIVE)
    if scope is not ALL:
        q = q.where(User.id.in_(scope))
    if filters.get("user_ids"):
        q = q.where(User.id.in_(filters["user_ids"]))
    if filters.get("network_ids"):
        q = q.where(
            User.id.in_(
                select(UserNetwork.user_id).where(
                    UserNetwork.network_id.in_(filters["network_ids"])
                )
            )
        )
    for key in ("coach_id", "instructor_id"):
        if filters.get(key):
            q = q.where(User.id.in_(await team_of(session, filters[key]) or {0}))
    today = timeutil.msk_today()
    tomorrow = today + timedelta(days=1)

    def on(day, statuses):
        return select(Shift.user_id).where(Shift.day == day, Shift.status.in_(statuses))

    if aud.get("working_today") is True:
        q = q.where(User.id.in_(on(today, (ShiftStatus.CONFIRMED,))))
    elif aud.get("working_today") is False:
        q = q.where(User.id.not_in(on(today, (ShiftStatus.CONFIRMED,))))
    if aud.get("scheduled_tomorrow") is True:
        q = q.where(User.id.in_(on(tomorrow, (ShiftStatus.CONFIRMED,))))
    elif aud.get("scheduled_tomorrow") is False:
        q = q.where(User.id.not_in(on(tomorrow, (ShiftStatus.CONFIRMED,))))
    if aud.get("no_status_tomorrow"):
        q = q.where(
            User.id.not_in(on(tomorrow, ACTIVE_SHIFT)),
            User.id.not_in(select(DayOff.user_id).where(DayOff.day == tomorrow)),
        )
    return list((await session.execute(q.order_by(User.last_name, User.first_name))).scalars())


def _check_text(text: str, with_photo: bool) -> str:
    clean = sanitize(text)
    if not clean:
        raise AppError("empty", "Пустой текст рассылки")
    limit = MAX_CAPTION if with_photo else MAX_TEXT
    if plain_length(clean) > limit:
        raise AppError(
            "too_long",
            f"Текст длиннее {limit} символов" + (" (подпись к фото)" if with_photo else ""),
        )
    return clean


async def save_draft(
    session: AsyncSession, actor: Actor, bid: int | None, data: dict[str, Any]
) -> Broadcast:
    actor.require(Perm.BROADCASTS_SEND)
    if bid is None:
        b = Broadcast(author_id=actor.id)
        session.add(b)
    else:
        b = await session.get(Broadcast, bid, with_for_update=True)
        if b is None:
            raise NotFound("Рассылка не найдена")
        if b.status != "draft":
            raise Conflict("sent", "Рассылка уже отправлена")
    b.text = _check_text(data["text"], bool(b.photo_path))
    b.audience = data.get("audience") or {}
    b.require_ack = bool(data.get("require_ack"))
    b.allow_replies = bool(data.get("allow_replies"))
    await session.commit()
    return b


async def set_photo(
    session: AsyncSession, actor: Actor, bid: int, content: bytes | None
) -> Broadcast:
    actor.require(Perm.BROADCASTS_SEND)
    b = await session.get(Broadcast, bid, with_for_update=True)
    if b is None or b.status != "draft":
        raise NotFound("Черновик не найден")
    if content is None:
        b.photo_path = None
    else:
        rel, _, _ = await asyncio.to_thread(storage.save_photo, content)
        _check_text(b.text, True)
        b.photo_path = rel
    await session.commit()
    return b


async def preview(session: AsyncSession, actor: Actor, bid: int) -> dict[str, Any]:
    b = await _own(session, actor, bid)
    people = await resolve_audience(session, actor, b.audience)
    return {
        "broadcast": out(b),
        "count": len(people),
        "cannot_message": sum(1 for u in people if not u.can_message),
        "recipients": [
            {"user_id": u.id, "full_name": u.full_name, "can_message": u.can_message}
            for u in people[:500]
        ],
    }


async def _own(session: AsyncSession, actor: Actor, bid: int) -> Broadcast:
    actor.require(Perm.BROADCASTS_SEND)
    b = await session.get(Broadcast, bid)
    if b is None or (b.author_id != actor.id and not actor.is_owner):
        raise NotFound("Рассылка не найдена")
    return b


async def send(session: AsyncSession, actor: Actor, bid: int, expected_count: int) -> Broadcast:
    """Отправка только после подтверждения с числом получателей; повтор не дублирует."""
    b = await _own(session, actor, bid)
    b = await session.get(Broadcast, bid, with_for_update=True, populate_existing=True)
    if b.status != "draft":
        return b
    people = await resolve_audience(session, actor, b.audience)
    if len(people) != expected_count:
        raise Conflict(
            "changed", f"Получателей стало {len(people)} — проверьте предпросмотр ещё раз"
        )
    if not people:
        raise AppError("no_recipients", "Нет получателей")
    markup = None
    if b.require_ack:
        markup = {"inline_keyboard": [[{"text": "Я ознакомился", "callback_data": f"ack:{b.id}"}]]}
    text = b.text
    if b.allow_replies:
        text += "\n\n<i>Чтобы ответить, используйте «Ответить» на это сообщение.</i>"
    for u in people:
        r = BroadcastRecipient(broadcast_id=b.id, user_id=u.id)
        session.add(r)
        await session.flush()
        if not u.can_message:
            r.status, r.error = "failed", "Сотрудник не запускал бота или заблокировал его"
            continue
        payload: dict[str, Any] = {"parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        if b.photo_path:
            payload.update(photo_path=str(storage.photo_path(b.photo_path)), caption=text)
            method = "sendPhoto"
        else:
            payload["text"] = text
            method = "sendMessage"
        await outbox.enqueue_raw(
            session,
            dedup_key=f"broadcast:{b.id}:{u.id}",
            chat_id=u.telegram_id,
            method=method,
            payload=payload,
            related_user_id=u.id,
            ref_type="broadcast_recipient",
            ref_id=r.id,
        )
    b.status = "sent"
    b.sent_at = timeutil.now_utc()
    b.recipients_count = len(people)
    audit.record(
        session,
        category=Category.BROADCASTS,
        action="broadcast.sent",
        actor_id=actor.id,
        object_type="broadcast",
        object_id=b.id,
        new={"recipients": len(people), "audience": b.audience, "require_ack": b.require_ack},
    )
    await session.commit()
    return b


async def on_recipient_result(session: AsyncSession, rid: int, ok: bool, message_id, error) -> None:
    r = await session.get(BroadcastRecipient, rid)
    if r is None:
        return
    if ok:
        r.status, r.tg_message_id, r.sent_at, r.error = "sent", message_id, timeutil.now_utc(), None
    else:
        r.status, r.error = "failed", error


async def acknowledge(session: AsyncSession, user: User, bid: int) -> bool:
    r = await session.scalar(
        select(BroadcastRecipient)
        .where(BroadcastRecipient.broadcast_id == bid, BroadcastRecipient.user_id == user.id)
        .with_for_update()
    )
    if r is None:
        return False
    b = await session.get(Broadcast, bid)
    if not b.require_ack:
        return False
    if r.acked_at is None:
        r.acked_at = timeutil.now_utc()
        await session.commit()
    return True


async def save_reply(
    session: AsyncSession,
    user: User,
    reply_to_message_id: int,
    text: str | None,
    media_kind: str | None,
    file_id: str | None,
) -> bool:
    """Ответ сотрудника на конкретную рассылку («Ответить» на её сообщение в боте)."""
    r = await session.scalar(
        select(BroadcastRecipient).where(
            BroadcastRecipient.user_id == user.id,
            BroadcastRecipient.tg_message_id == reply_to_message_id,
        )
    )
    if r is None:
        return False
    b = await session.get(Broadcast, r.broadcast_id)
    if not b.allow_replies:
        return False
    session.add(
        BroadcastReply(
            broadcast_id=b.id,
            user_id=user.id,
            text=text,
            media_kind=media_kind,
            media_file_id=file_id,
        )
    )
    await session.commit()
    return True


def out(b: Broadcast) -> dict[str, Any]:
    return {
        k: getattr(b, k)
        for k in (
            "id",
            "author_id",
            "text",
            "audience",
            "require_ack",
            "allow_replies",
            "status",
            "recipients_count",
            "created_at",
            "sent_at",
        )
    } | {"has_photo": bool(b.photo_path)}


async def details(session: AsyncSession, actor: Actor, bid: int) -> dict[str, Any]:
    b = await _own(session, actor, bid)
    rows = (
        await session.execute(
            select(BroadcastRecipient, User)
            .join(User, User.id == BroadcastRecipient.user_id)
            .where(BroadcastRecipient.broadcast_id == b.id)
            .order_by(User.last_name)
        )
    ).all()
    replies = (
        await session.execute(
            select(BroadcastReply, User)
            .join(User, User.id == BroadcastReply.user_id)
            .where(BroadcastReply.broadcast_id == b.id)
            .order_by(BroadcastReply.id)
        )
    ).all()
    counts = {"queued": 0, "sent": 0, "failed": 0}
    for r, _ in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {
        "broadcast": out(b),
        "counts": {**counts, "acked": sum(1 for r, _ in rows if r.acked_at), "total": len(rows)},
        "recipients": [
            {
                "user_id": u.id,
                "full_name": u.full_name,
                "status": r.status,
                "error": r.error,
                "acked_at": r.acked_at,
                "sent_at": r.sent_at,
            }
            for r, u in rows
        ],
        "replies": [
            {
                "id": x.id,
                "full_name": u.full_name,
                "text": x.text,
                "media_kind": x.media_kind,
                "created_at": x.created_at,
            }
            for x, u in replies
        ],
    }


async def list_mine(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    actor.require(Perm.BROADCASTS_SEND)
    q = select(Broadcast).order_by(Broadcast.id.desc()).limit(100)
    if not actor.is_owner:
        q = q.where(Broadcast.author_id == actor.id)
    return [out(b) for b in (await session.execute(q)).scalars()]


async def received(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Broadcast, BroadcastRecipient)
            .join(BroadcastRecipient, BroadcastRecipient.broadcast_id == Broadcast.id)
            .where(BroadcastRecipient.user_id == user.id)
            .order_by(Broadcast.id.desc())
            .limit(50)
        )
    ).all()
    return [
        {
            "id": b.id,
            "text": b.text,
            "sent_at": b.sent_at,
            "require_ack": b.require_ack,
            "acked_at": r.acked_at,
        }
        for b, r in rows
    ]
