"""Telegram-группы, проверка членства, прогресс новичка, приглашения (ТЗ 15)."""

import logging
from datetime import date, timedelta
from html import escape
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import (
    DONE_STATUSES,
    PURPOSES,
    AccountStatus,
    GroupInvite,
    GroupMembership,
    Role,
    Task,
    TgGroup,
    User,
)
from app.permissions import Perm
from app.services import audit, outbox
from app.services import settings as settings_svc
from app.services.access import ALL, Actor, get_target, scope_for
from app.services.audit import Category
from app.telegram_api import TelegramError, get_gateway

log = logging.getLogger(__name__)

# Назначения, которые может иметь только одна группа.
SINGLE_PURPOSES = ("training", "main", "audit", "summary")
REGULAR_DAYS = 3

MEMBER_STATUSES = ("member", "administrator", "creator", "restricted")


def group_out(g: TgGroup) -> dict[str, Any]:
    return {
        k: getattr(g, k)
        for k in (
            "id",
            "chat_id",
            "title",
            "chat_type",
            "is_forum",
            "purpose",
            "status",
            "bot_rights",
            "last_checked_at",
            "check_error",
            "invite_cooldown_hours",
            "created_at",
        )
    }


# --- подключение --------------------------------------------------------------------


async def upsert_group(
    session: AsyncSession,
    *,
    chat_id: int,
    title: str,
    chat_type: str | None,
    is_forum: bool,
    bot_status: str,
) -> TgGroup:
    """Бот добавлен в группу или его статус изменился (событие my_chat_member)."""
    status = "connected" if bot_status in ("member", "administrator", "creator") else "left"
    g = await session.scalar(select(TgGroup).where(TgGroup.chat_id == chat_id).with_for_update())
    if g is None:
        g = TgGroup(
            chat_id=chat_id,
            title=title or str(chat_id),
            chat_type=chat_type,
            is_forum=is_forum,
            status=status,
        )
        session.add(g)
        await session.flush()
        action = "group.discovered"
    else:
        action = "group.bot_status"
        g.title = title or g.title
        g.chat_type = chat_type
        g.is_forum = is_forum
        g.status = status
    g.bot_rights = {**(g.bot_rights or {}), "status": bot_status}
    audit.record(
        session,
        category=Category.GROUPS,
        action=action,
        actor_id=None,
        object_type="tg_group",
        object_id=g.id,
        new={"chat_id": chat_id, "title": g.title, "bot_status": bot_status},
    )
    await session.commit()
    return g


async def add_by_chat_id(session: AsyncSession, actor: Actor, chat_id: int) -> TgGroup:
    actor.require_owner()
    try:
        chat = await get_gateway().get_chat(chat_id)
        rights = await get_gateway().bot_rights(chat_id)
    except TelegramError as e:
        raise AppError(
            "telegram_error",
            f"Telegram не дал данных о группе: {e}. Добавьте бота в группу администратором",
            409,
        ) from e
    g = await upsert_group(
        session,
        chat_id=chat_id,
        title=chat.get("title") or str(chat_id),
        chat_type=chat.get("type"),
        is_forum=chat.get("is_forum", False),
        bot_status=rights["status"],
    )
    g = await session.get(TgGroup, g.id)
    g.bot_rights = rights
    g.last_checked_at = timeutil.now_utc()
    await session.commit()
    return g


async def update_group(
    session: AsyncSession, actor: Actor, group_id: int, changes: dict[str, Any]
) -> TgGroup:
    actor.require_owner()
    g = await session.get(TgGroup, group_id, with_for_update=True)
    if g is None:
        raise NotFound("Группа не найдена")
    old = {"purpose": g.purpose, "invite_cooldown_hours": g.invite_cooldown_hours}
    if "purpose" in changes:
        purpose = changes["purpose"]
        if purpose not in PURPOSES:
            raise AppError("bad_purpose", "Неизвестное назначение")
        if purpose in SINGLE_PURPOSES:
            other = await session.scalar(
                select(TgGroup).where(TgGroup.purpose == purpose, TgGroup.id != g.id)
            )
            if other is not None:
                raise Conflict("purpose_taken", f"Назначение уже у группы «{other.title}»")
        g.purpose = purpose
    if "invite_cooldown_hours" in changes:
        v = changes["invite_cooldown_hours"]
        if v is not None and not 0 <= v <= 24 * 30:
            raise AppError("bad_cooldown", "Интервал — от 0 до 720 часов")
        g.invite_cooldown_hours = v
    new = {"purpose": g.purpose, "invite_cooldown_hours": g.invite_cooldown_hours}
    if new != old:
        audit.record(
            session,
            category=Category.GROUPS,
            action="group.updated",
            actor_id=actor.id,
            object_type="tg_group",
            object_id=g.id,
            old=old,
            new=new,
        )
    await session.commit()
    return g


async def check_rights(session: AsyncSession, actor: Actor, group_id: int) -> TgGroup:
    actor.require_owner()
    g = await session.get(TgGroup, group_id, with_for_update=True)
    if g is None:
        raise NotFound("Группа не найдена")
    g.last_checked_at = timeutil.now_utc()
    try:
        g.bot_rights = await get_gateway().bot_rights(g.chat_id)
        g.check_error = None
        g.status = (
            "connected"
            if g.bot_rights["status"] in ("member", "administrator", "creator")
            else "left"
        )
    except TelegramError as e:
        g.check_error = str(e)
        g.status = "error"
    await session.commit()
    return g


async def send_test(session: AsyncSession, actor: Actor, group_id: int) -> dict[str, Any]:
    """Тестовое сообщение: результат — то, что ответил Telegram, а не предположение."""
    actor.require_owner()
    g = await session.get(TgGroup, group_id)
    if g is None:
        raise NotFound("Группа не найдена")
    try:
        mid = await get_gateway().send(
            "sendMessage",
            g.chat_id,
            {"text": f"Тестовое сообщение от бота ({timeutil.format_msk(timeutil.now_utc())})"},
        )
    except TelegramError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "message_id": mid}


async def by_purpose(session: AsyncSession, purpose: str) -> TgGroup | None:
    return await session.scalar(select(TgGroup).where(TgGroup.purpose == purpose))


# --- новички ------------------------------------------------------------------------


async def worked_days(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, list[tuple[date, int]]]:
    """Рабочие дни (МСК) с хотя бы одной выполненной единицей.

    Несколько заданий за день — один рабочий день.
    """
    day = func.date(func.timezone("Europe/Moscow", Task.completed_at))
    rows = await session.execute(
        select(Task.assignee_id, day, func.sum(Task.units))
        .where(
            Task.assignee_id.in_(user_ids or [0]),
            Task.status.in_(DONE_STATUSES),
            Task.completed_at.is_not(None),
        )
        .group_by(Task.assignee_id, day)
        .order_by(Task.assignee_id, day)
    )
    out: dict[int, list[tuple[date, int]]] = {}
    for uid, d, n in rows:
        out.setdefault(uid, []).append((d, int(n)))
    return out


def progress_from_days(days: list[tuple[date, int]]) -> dict[str, Any]:
    first = days[:REGULAR_DAYS]
    return {
        "days": len(first),
        "label": f"{len(first)}/{REGULAR_DAYS}",
        "units": sum(n for _, n in first),
        "is_regular": len(days) >= REGULAR_DAYS,
        "transition_date": days[REGULAR_DAYS - 1][0] if len(days) >= REGULAR_DAYS else None,
    }


async def sync_regular(session: AsyncSession, user: User, progress: dict[str, Any]) -> None:
    """Зафиксировать дату перехода. Отсутствие активности её не отменяет — дни исторические;
    исправление истории (например, отмена заданий) пересчитывает дату с записью в аудит."""
    target = progress["transition_date"]
    if user.became_regular_on == target:
        return
    audit.record(
        session,
        category=Category.GROUPS,
        action="newbie.transition" if user.became_regular_on is None else "newbie.recalculated",
        actor_id=None,
        target_user_id=user.id,
        object_type="user",
        object_id=user.id,
        old={"became_regular_on": user.became_regular_on},
        new={"became_regular_on": target},
    )
    user.became_regular_on = target


async def recalc_all(session: AsyncSession) -> int:
    users = (
        (
            await session.execute(
                select(User).where(User.status == AccountStatus.ACTIVE, User.role != Role.OWNER)
            )
        )
        .scalars()
        .all()
    )
    days = await worked_days(session, [u.id for u in users])
    changed = 0
    for u in users:
        before = u.became_regular_on
        await sync_regular(session, u, progress_from_days(days.get(u.id, [])))
        changed += before != u.became_regular_on
    await session.commit()
    return changed


# --- членство -----------------------------------------------------------------------


async def _set_membership(
    session: AsyncSession,
    group_id: int,
    user_id: int,
    status: str,
    source: str,
    detail: str | None = None,
) -> None:
    await session.execute(
        insert(GroupMembership)
        .values(
            group_id=group_id,
            user_id=user_id,
            status=status,
            source=source,
            detail=detail,
            checked_at=timeutil.now_utc(),
        )
        .on_conflict_do_update(
            index_elements=["group_id", "user_id"],
            set_={
                "status": status,
                "source": source,
                "detail": detail,
                "checked_at": timeutil.now_utc(),
            },
        )
    )


async def check_membership(
    session: AsyncSession, actor: Actor, group_id: int, user_ids: list[int] | None = None
) -> dict[str, int]:
    """Проверить членство по Telegram ID. Ошибка Telegram даёт «Не удалось проверить»."""
    actor.require(Perm.GROUPS_VIEW)
    g = await session.get(TgGroup, group_id)
    if g is None:
        raise NotFound("Группа не найдена")
    people = await _people(session, actor, Perm.GROUPS_VIEW)
    if user_ids:
        people = [u for u in people if u.id in set(user_ids)]
    result = {"member": 0, "not_member": 0, "unknown": 0}
    gw = get_gateway()
    for u in people:
        try:
            st = await gw.member_status(g.chat_id, u.telegram_id)
            status = "member" if st in MEMBER_STATUSES else "not_member"
            detail = st
        except TelegramError as e:
            status, detail = "unknown", str(e)
        await _set_membership(session, g.id, u.id, status, "check", detail)
        if status == "member":
            await _mark_joined(session, g.id, u.id)
        result[status] += 1
    audit.record(
        session,
        category=Category.GROUPS,
        action="membership.checked",
        actor_id=actor.id,
        object_type="tg_group",
        object_id=g.id,
        new=result,
    )
    await session.commit()
    return result


async def on_member_event(
    session: AsyncSession, chat_id: int, telegram_id: int, new_status: str
) -> None:
    """Событие chat_member от Telegram: вступление или выход участника."""
    g = await session.scalar(select(TgGroup).where(TgGroup.chat_id == chat_id))
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if g is None or user is None:
        return
    status = "member" if new_status in MEMBER_STATUSES else "not_member"
    await _set_membership(session, g.id, user.id, status, "event", new_status)
    if status == "member":
        await _mark_joined(session, g.id, user.id)
    audit.record(
        session,
        category=Category.GROUPS,
        action="membership.event",
        actor_id=None,
        target_user_id=user.id,
        object_type="tg_group",
        object_id=g.id,
        new={"status": status, "telegram_status": new_status},
    )
    await session.commit()


async def _mark_joined(session: AsyncSession, group_id: int, user_id: int) -> None:
    inv = await session.scalar(
        select(GroupInvite)
        .where(
            GroupInvite.group_id == group_id,
            GroupInvite.user_id == user_id,
            GroupInvite.status.in_(["prepared", "sent", "send_failed"]),
        )
        .order_by(GroupInvite.id.desc())
        .limit(1)
    )
    if inv is not None:
        inv.status = "joined"
        inv.joined_at = timeutil.now_utc()


async def _people(session: AsyncSession, actor: Actor, perm: Perm) -> list[User]:
    scope = await scope_for(session, actor, perm)
    q = select(User).where(User.status == AccountStatus.ACTIVE, User.role != Role.OWNER)
    if scope is not ALL:
        q = q.where(User.id.in_(scope))
    return list((await session.execute(q.order_by(User.last_name, User.first_name))).scalars())


def recommendations(progress: dict[str, Any], training: str | None, main: str | None) -> list[str]:
    """Правила членства (ТЗ 15.4).

    training/main — member | not_member | unknown | None (группа не подключена).
    """
    out: list[str] = []
    if not progress["is_regular"]:
        if training == "not_member":
            out.append("Необходимо добавить в группу стажировки")
        elif training == "unknown":
            out.append("Группа стажировки: не удалось проверить")
        return out
    if training == "member":
        out.append("Необходимо убрать из группы стажировки")
    if main == "not_member":
        out.append("Необходимо добавить в основную группу")
    elif main == "unknown":
        out.append("Основная группа: не удалось проверить")
    elif main == "member":
        out.append("Состоит в группе")
    return out


async def overview(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    actor.require(Perm.GROUPS_VIEW)
    groups = (await session.execute(select(TgGroup).order_by(TgGroup.title))).scalars().all()
    training = next((g for g in groups if g.purpose == "training"), None)
    main = next((g for g in groups if g.purpose == "main"), None)
    people = await _people(session, actor, Perm.GROUPS_VIEW)
    days = await worked_days(session, [u.id for u in people])
    ms = {
        (m.group_id, m.user_id): m
        for m in (
            await session.execute(
                select(GroupMembership).where(
                    GroupMembership.user_id.in_([u.id for u in people] or [0])
                )
            )
        ).scalars()
    }
    last_inv = {}
    for inv in (
        await session.execute(
            select(GroupInvite)
            .where(GroupInvite.user_id.in_([u.id for u in people] or [0]))
            .order_by(GroupInvite.id)
        )
    ).scalars():
        last_inv[(inv.group_id, inv.user_id)] = inv

    def st(g: TgGroup | None, uid: int) -> str | None:
        if g is None:
            return None
        m = ms.get((g.id, uid))
        return m.status if m else "unknown"

    rows = []
    for u in people:
        prog = progress_from_days(days.get(u.id, []))
        await sync_regular(session, u, prog)
        t, m = st(training, u.id), st(main, u.id)
        rows.append(
            {
                "user_id": u.id,
                "full_name": u.full_name,
                "can_message": u.can_message,
                "progress": prog,
                "became_regular_on": u.became_regular_on,
                "memberships": {
                    str(g.id): {
                        "status": (
                            ms.get((g.id, u.id)).status if ms.get((g.id, u.id)) else "unknown"
                        ),
                        "checked_at": ms.get((g.id, u.id)).checked_at
                        if ms.get((g.id, u.id))
                        else None,
                        "invite": invite_out(last_inv[(g.id, u.id)])
                        if (g.id, u.id) in last_inv
                        else None,
                    }
                    for g in groups
                },
                "recommendations": recommendations(prog, t, m),
            }
        )
    await session.commit()
    return {"groups": [group_out(g) for g in groups], "people": rows}


# --- приглашения --------------------------------------------------------------------


def invite_out(i: GroupInvite) -> dict[str, Any]:
    return {
        k: getattr(i, k)
        for k in (
            "id",
            "group_id",
            "user_id",
            "invite_link",
            "status",
            "error",
            "created_at",
            "sent_at",
            "joined_at",
        )
    }


OWNER_INVITE_PURPOSES = ("audit", "photoreports", "summary")


async def invite(session: AsyncSession, actor: Actor, group_id: int, user_id: int) -> GroupInvite:
    """Пригласить в выбранную группу. Ошибка Telegram сохраняется и показывается как ошибка."""
    user = await get_target(session, actor, Perm.GROUPS_INVITE, user_id)
    g = await session.get(TgGroup, group_id)
    if g is None:
        raise NotFound("Группа не найдена")
    if g.purpose in OWNER_INVITE_PURPOSES and not actor.is_owner:
        # В архив аудита и группы фотоотчётов попадают данные всей компании.
        raise Forbidden("owner_only", "В эту группу приглашает только владелец")
    if g.status != "connected":
        raise Conflict("group_unavailable", "Бот не состоит в этой группе — приглашение невозможно")
    if g.bot_rights and g.bot_rights.get("can_invite_users") is False:
        raise Conflict("no_rights", "У бота нет права приглашать участников в эту группу")

    await session.get(User, user.id, with_for_update=True)  # одно приглашение за раз на человека
    cooldown = g.invite_cooldown_hours
    if cooldown is None:
        cooldown = await settings_svc.get(session, "invite_cooldown_hours")
    last = await session.scalar(
        select(GroupInvite)
        .where(
            GroupInvite.group_id == g.id,
            GroupInvite.user_id == user.id,
            GroupInvite.status.in_(["prepared", "sent"]),
        )
        .order_by(GroupInvite.id.desc())
        .limit(1)
    )
    if last is not None and timeutil.now_utc() - last.created_at < timedelta(hours=cooldown):
        raise Conflict(
            "too_soon",
            f"Приглашение уже отправлялось {timeutil.format_msk(last.created_at)}. "
            f"Повторно — не раньше чем через {cooldown} ч",
        )

    inv = GroupInvite(group_id=g.id, user_id=user.id, created_by_id=actor.id)
    session.add(inv)
    await session.flush()
    try:
        inv.invite_link = await get_gateway().create_invite_link(
            g.chat_id, name=f"Wakeup {user.id}", expire_seconds=7 * 24 * 3600
        )
    except TelegramError as e:
        inv.status = "send_failed"
        inv.error = f"Не удалось создать ссылку: {e}"
    else:
        if user.can_message:
            await outbox.enqueue_raw(
                session,
                dedup_key=f"invite:{inv.id}",
                chat_id=user.telegram_id,
                method="sendMessage",
                payload={
                    "text": f"Приглашение в группу «{escape(g.title)}»:\n{inv.invite_link}",
                    "parse_mode": "HTML",
                },
                related_user_id=user.id,
                ref_type="invite",
                ref_id=inv.id,
            )
        else:
            inv.status = "send_failed"
            inv.error = "Сотрудник не запускал бота или заблокировал его — передайте ссылку вручную"
    audit.record(
        session,
        category=Category.GROUPS,
        action="invite.created",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="group_invite",
        object_id=inv.id,
        new={"group": g.title, "status": inv.status, "error": inv.error},
        result="success" if inv.status == "prepared" else "error",
    )
    await session.commit()
    return inv


async def invite_bulk(
    session: AsyncSession, actor: Actor, group_id: int, user_ids: list[int]
) -> list[dict[str, Any]]:
    """Массовое приглашение: итог по каждому сотруднику."""
    out = []
    for uid in user_ids:
        try:
            inv = await invite(session, actor, group_id, uid)
            out.append({"user_id": uid, **invite_out(inv)})
        except AppError as e:
            await session.rollback()
            # После отката объекты сессии сброшены — перечитываем оператора.
            await session.refresh(actor.user)
            out.append({"user_id": uid, "status": "error", "error": e.message})
    return out


async def on_invite_result(
    session: AsyncSession, invite_id: int, ok: bool, message_id, error
) -> None:
    inv = await session.get(GroupInvite, invite_id)
    if inv is None or inv.status == "joined":
        return
    if ok:
        inv.status = "sent"
        inv.sent_at = timeutil.now_utc()
        inv.error = None
    else:
        inv.status = "send_failed"
        inv.error = f"Telegram не доставил сообщение: {error}"
    audit.record(
        session,
        category=Category.GROUPS,
        action="invite.delivery",
        actor_id=None,
        target_user_id=inv.user_id,
        object_type="group_invite",
        object_id=inv.id,
        new={"status": inv.status},
        reason=inv.error,
        result="success" if ok else "error",
    )
