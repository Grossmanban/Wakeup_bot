"""Telegram-архив аудита (ТЗ 14): дополнительная копия журнала в супергруппе с темами.

Источник — таблица audit_events. Задача по расписанию переносит новые записи в очередь
отправки по курсору; если Telegram недоступен, записи остаются в БД и уйдут позже.
"""

from html import escape
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.errors import Conflict, NotFound
from app.models import AuditEvent, SystemSetting, TgGroup, User
from app.services import outbox
from app.services.access import Actor
from app.telegram_api import TelegramError, get_gateway

CURSOR_KEY = "_audit_archive_cursor"
BATCH = 50

TOPICS = {
    "users": "Пользователи и доступы",
    "penalties": "Штрафы и удержания",
    "bonuses": "Бонусы",
    "tasks": "Задания и модерация",
    "photos": "Фото",
    "schedule": "График и точки",
    "finance": "Финансы и выплаты",
    "settings": "Настройки",
    "groups": "Членство в группах и приглашения",
    "system": "Ошибки доставки и системные события",
    "broadcasts": "Рассылки",
}


async def setup_topics(session: AsyncSession, actor: Actor, group_id: int) -> TgGroup:
    """Создать недостающие темы в супергруппе архива."""
    actor.require_owner()
    g = await session.get(TgGroup, group_id, with_for_update=True)
    if g is None:
        raise NotFound("Группа не найдена")
    if g.purpose != "audit":
        raise Conflict("not_audit", "Сначала назначьте группе назначение «Архив аудита»")
    topics = dict(g.topics or {})
    errors = []
    if g.is_forum:
        for key, title in TOPICS.items():
            if key in topics:
                continue
            try:
                topics[key] = await get_gateway().create_forum_topic(g.chat_id, title)
            except TelegramError as e:
                errors.append(f"{title}: {e}")
    g.topics = topics
    g.check_error = "; ".join(errors) or None
    await _ensure_cursor(session)
    await session.commit()
    return g


async def _ensure_cursor(session: AsyncSession) -> int:
    row = await session.get(SystemSetting, CURSOR_KEY)
    if row is None:
        # Архив начинается с момента подключения — без лавины старых записей.
        last = await session.scalar(select(func.coalesce(func.max(AuditEvent.id), 0)))
        row = SystemSetting(key=CURSOR_KEY, value=int(last))
        session.add(row)
        await session.flush()
    return int(row.value)


def format_event(ev: AuditEvent, users: dict[int, User]) -> str:
    def who(uid: int | None) -> str:
        if uid is None:
            return "система"
        u = users.get(uid)
        return f"{escape(u.full_name)} (ID {u.id})" if u else f"ID {uid}"

    def short(v: Any) -> str:
        s = "—" if v is None else str(v)
        return escape(s[:300] + ("…" if len(s) > 300 else ""))

    lines = [
        f"<b>{escape(TOPICS.get(ev.category, ev.category))}</b> · событие #{ev.id}",
        f"Действие: {escape(ev.action)}",
        f"Время: {timeutil.format_msk(ev.occurred_at)}",
        f"Инициатор: {who(ev.actor_id)}",
    ]
    if ev.target_user_id:
        lines.append(f"Сотрудник: {who(ev.target_user_id)}")
    if ev.object_type:
        lines.append(f"Объект: {escape(ev.object_type)} {escape(ev.object_id or '')}")
    if ev.reason:
        lines.append(f"Причина: {escape(ev.reason)}")
    if ev.old_value is not None or ev.new_value is not None:
        lines.append(f"Было: {short(ev.old_value)}")
        lines.append(f"Стало: {short(ev.new_value)}")
    lines.append(f"Результат: {'успешно' if ev.result == 'success' else escape(ev.result)}")
    return "\n".join(lines)


async def export_batch(session: AsyncSession) -> int:
    g = await session.scalar(
        select(TgGroup).where(TgGroup.purpose == "audit", TgGroup.status == "connected")
    )
    if g is None:
        return 0
    cursor_row = await session.get(SystemSetting, CURSOR_KEY, with_for_update=True)
    if cursor_row is None:
        await _ensure_cursor(session)
        await session.commit()
        return 0
    cursor = int(cursor_row.value)
    events = (
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.id > cursor)
                .order_by(AuditEvent.id)
                .limit(BATCH)
            )
        )
        .scalars()
        .all()
    )
    if not events:
        return 0
    ids = {i for e in events for i in (e.actor_id, e.target_user_id) if i}
    users = {
        u.id: u
        for u in (await session.execute(select(User).where(User.id.in_(ids or {0})))).scalars()
    }
    for ev in events:
        payload: dict[str, Any] = {"text": format_event(ev, users), "parse_mode": "HTML"}
        thread = (g.topics or {}).get(ev.category) or (g.topics or {}).get("system")
        if thread:
            payload["message_thread_id"] = thread
        await outbox.enqueue_raw(
            session,
            dedup_key=f"audit_archive:{ev.id}",
            chat_id=g.chat_id,
            method="sendMessage",
            payload=payload,
        )
    cursor_row.value = events[-1].id
    await session.commit()
    return len(events)
