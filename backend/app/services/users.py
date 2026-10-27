"""Регистрация, заявки, смена ФИО, роли, статусы и разрешения (ТЗ 2, 3)."""

import re
from html import escape

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import (
    AccountStatus,
    NameChangeRequest,
    Network,
    RegistrationRequest,
    RequestStatus,
    Role,
    User,
    UserPermission,
)
from app.permissions import Perm
from app.security.telegram_auth import TelegramUser
from app.services import audit, outbox
from app.services.access import ALL, Actor, get_target, scope_for
from app.services.audit import Category
from app.timeutil import now_utc

STATUS_MESSAGES = {
    AccountStatus.DISABLED: "Ваш аккаунт отключён. Обратитесь к администрации для его включения",
    AccountStatus.BLOCKED: "Ваш аккаунт заблокирован",
}
NO_PRODUCTS_MESSAGE = "Вам пока не выданы доступы к продуктам. Обратитесь к администрации"
NO_APP_ACCESS_MESSAGE = "Доступ к приложению закрыт. Обратитесь к администрации"

ROLE_TITLES = {
    Role.OWNER: "Владелец",
    Role.ADMIN: "Администратор",
    Role.COACH: "Тренер",
    Role.INSTRUCTOR: "Инструктор",
    Role.EMPLOYEE: "Сотрудник",
}

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё' -]*$")


def clean_name(value: str, field: str) -> str:
    v = " ".join(value.split())
    if not v:
        raise AppError("invalid_name", f"Укажите {field}")
    if len(v) > 64 or not _NAME_RE.match(v):
        raise AppError("invalid_name", f"Недопустимые символы: {field}")
    return v


# --- вход ---------------------------------------------------------------------------


async def upsert_from_telegram(
    session: AsyncSession, tg: TelegramUser, *, started_bot: bool = False
) -> User:
    """Найти или создать профиль по Telegram ID. Один Telegram ID — один профиль."""
    values = {
        "telegram_id": tg.id,
        "username": tg.username,
        "tg_first_name": tg.first_name,
        "tg_last_name": tg.last_name,
        "role": Role.EMPLOYEE,
        "status": AccountStatus.UNREGISTERED,
        "app_access": True,
    }
    update = {
        "username": tg.username,
        "tg_first_name": tg.first_name,
        "tg_last_name": tg.last_name,
    }
    if started_bot:
        values["bot_started_at"] = update["bot_started_at"] = now_utc()
        update["bot_blocked_at"] = None
    stmt = (
        insert(User)
        .values(**values)
        .on_conflict_do_update(index_elements=["telegram_id"], set_=update)
        .returning(User.id)
    )
    user_id = (await session.execute(stmt)).scalar_one()
    user = await session.get(User, user_id, populate_existing=True, with_for_update=True)
    assert user is not None

    if tg.id in get_settings().owner_telegram_ids and (
        user.role != Role.OWNER or user.status != AccountStatus.ACTIVE
    ):
        old = {"role": user.role, "status": user.status}
        user.role = Role.OWNER
        user.status = AccountStatus.ACTIVE
        audit.record(
            session,
            category=Category.USERS,
            action="owner.bootstrap",
            actor_id=None,
            target_user_id=user.id,
            object_type="user",
            object_id=user.id,
            old=old,
            new={"role": user.role, "status": user.status},
            reason="Telegram ID указан в OWNER_TELEGRAM_IDS",
        )
    await session.flush()
    return user


async def _reviewers(session: AsyncSession, perm: Perm) -> list[User]:
    rows = await session.execute(
        select(User)
        .outerjoin(
            UserPermission,
            (UserPermission.user_id == User.id) & (UserPermission.permission == perm.value),
        )
        .where(
            User.status == AccountStatus.ACTIVE,
            or_(User.role == Role.OWNER, UserPermission.id.is_not(None)),
        )
        .distinct()
    )
    return list(rows.scalars())


# --- регистрация --------------------------------------------------------------------


async def submit_registration(
    session: AsyncSession, user: User, first_name: str, last_name: str
) -> RegistrationRequest | None:
    first = clean_name(first_name, "имя")
    last = clean_name(last_name, "фамилию")

    if user.role == Role.OWNER:
        # Владельцу некому подавать заявку: имя сохраняется сразу.
        old = {"first_name": user.first_name, "last_name": user.last_name}
        user.first_name, user.last_name = first, last
        audit.record(
            session,
            category=Category.USERS,
            action="profile.name_set",
            actor_id=user.id,
            target_user_id=user.id,
            object_type="user",
            object_id=user.id,
            old=old,
            new={"first_name": first, "last_name": last},
        )
        await session.commit()
        return None

    if user.status in (AccountStatus.DISABLED, AccountStatus.BLOCKED):
        raise Forbidden(f"account_{user.status.value}", STATUS_MESSAGES[user.status])
    if user.status == AccountStatus.ACTIVE:
        raise Conflict("already_registered", "Вы уже зарегистрированы. ФИО меняется через заявку")

    if user.status == AccountStatus.PENDING:
        req = (
            await session.execute(
                select(RegistrationRequest)
                .where(
                    RegistrationRequest.user_id == user.id,
                    RegistrationRequest.status == RequestStatus.PENDING,
                )
                .with_for_update()
            )
        ).scalar_one()
        if (req.first_name, req.last_name) != (first, last):
            old = {"first_name": req.first_name, "last_name": req.last_name}
            req.first_name, req.last_name = first, last
            audit.record(
                session,
                category=Category.USERS,
                action="registration.updated",
                actor_id=user.id,
                target_user_id=user.id,
                object_type="registration_request",
                object_id=req.id,
                old=old,
                new={"first_name": first, "last_name": last},
            )
        await session.commit()
        return req

    # UNREGISTERED или REJECTED — новая заявка.
    req = RegistrationRequest(user_id=user.id, first_name=first, last_name=last)
    session.add(req)
    user.status = AccountStatus.PENDING
    user.status_reason = None
    await session.flush()
    audit.record(
        session,
        category=Category.USERS,
        action="registration.submitted",
        actor_id=user.id,
        target_user_id=user.id,
        object_type="registration_request",
        object_id=req.id,
        new={"first_name": first, "last_name": last, "telegram_id": user.telegram_id},
    )
    uname = f" (@{escape(user.username)})" if user.username else ""
    for r in await _reviewers(session, Perm.REGISTRATIONS_REVIEW):
        await outbox.enqueue(
            session,
            dedup_key=f"reg:{req.id}:notify:{r.id}",
            chat_id=r.telegram_id,
            related_user_id=r.id,
            text=(
                "<b>Новая заявка на регистрацию</b>\n"
                f"{escape(last)} {escape(first)}{uname}\nTelegram ID: {user.telegram_id}"
            ),
            open_app_button="Рассмотреть",
        )
    await session.commit()
    return req


async def decide_registration(
    session: AsyncSession, actor: Actor, request_id: int, *, approve: bool, reason: str | None
) -> RegistrationRequest:
    # Новые заявители ещё не состоят ни в одной сети, поэтому заявки видит
    # каждый, у кого есть это разрешение, независимо от ограничения по сети.
    actor.require(Perm.REGISTRATIONS_REVIEW)
    req = await session.get(RegistrationRequest, request_id, with_for_update=True)
    if req is None:
        raise NotFound("Заявка не найдена")
    if req.status != RequestStatus.PENDING:
        raise Conflict("already_decided", "Заявка уже рассмотрена")
    user = await session.get(User, req.user_id, with_for_update=True)
    assert user is not None
    reason = (reason or "").strip() or None

    req.decided_at = now_utc()
    req.decided_by_id = actor.id
    req.reason = reason
    if approve:
        req.status = RequestStatus.APPROVED
        user.first_name, user.last_name = req.first_name, req.last_name
        user.status = AccountStatus.ACTIVE
        user.status_reason = None
        text = "Ваша заявка одобрена. Добро пожаловать!"
    else:
        req.status = RequestStatus.REJECTED
        user.status = AccountStatus.REJECTED
        user.status_reason = reason
        text = "Ваша заявка отклонена." + (f"\nПричина: {escape(reason)}" if reason else "")

    audit.record(
        session,
        category=Category.USERS,
        action="registration.approved" if approve else "registration.rejected",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="registration_request",
        object_id=req.id,
        old={"status": AccountStatus.PENDING},
        new={"status": user.status},
        reason=reason,
    )
    await outbox.enqueue(
        session,
        dedup_key=f"reg:{req.id}:decision",
        chat_id=user.telegram_id,
        related_user_id=user.id,
        text=text,
        open_app_button="Открыть приложение" if approve else None,
    )
    await session.commit()
    return req


# --- смена ФИО ----------------------------------------------------------------------


async def request_name_change(
    session: AsyncSession, user: User, first_name: str, last_name: str
) -> NameChangeRequest | None:
    first = clean_name(first_name, "имя")
    last = clean_name(last_name, "фамилию")
    if user.role == Role.OWNER:
        return await submit_registration(session, user, first, last)
    if user.status != AccountStatus.ACTIVE:
        raise Conflict("not_active", "Смена ФИО доступна после одобрения регистрации")
    if (user.first_name, user.last_name) == (first, last):
        raise AppError("same_name", "ФИО не изменилось")

    req = (
        await session.execute(
            select(NameChangeRequest)
            .where(
                NameChangeRequest.user_id == user.id,
                NameChangeRequest.status == RequestStatus.PENDING,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if req is None:
        req = NameChangeRequest(
            user_id=user.id,
            old_first_name=user.first_name,
            old_last_name=user.last_name,
            new_first_name=first,
            new_last_name=last,
        )
        session.add(req)
        action = "name_change.requested"
    else:
        req.new_first_name, req.new_last_name = first, last
        action = "name_change.updated"
    await session.flush()
    audit.record(
        session,
        category=Category.USERS,
        action=action,
        actor_id=user.id,
        target_user_id=user.id,
        object_type="name_change_request",
        object_id=req.id,
        old={"first_name": user.first_name, "last_name": user.last_name},
        new={"first_name": first, "last_name": last},
    )
    await session.commit()
    return req


async def cancel_name_change(session: AsyncSession, user: User) -> None:
    req = (
        await session.execute(
            select(NameChangeRequest)
            .where(
                NameChangeRequest.user_id == user.id,
                NameChangeRequest.status == RequestStatus.PENDING,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if req is None:
        return
    req.status = RequestStatus.CANCELLED
    req.decided_at = now_utc()
    req.decided_by_id = user.id
    audit.record(
        session,
        category=Category.USERS,
        action="name_change.cancelled",
        actor_id=user.id,
        target_user_id=user.id,
        object_type="name_change_request",
        object_id=req.id,
    )
    await session.commit()


async def decide_name_change(
    session: AsyncSession, actor: Actor, request_id: int, *, approve: bool, reason: str | None
) -> NameChangeRequest:
    req = await session.get(NameChangeRequest, request_id, with_for_update=True)
    if req is None:
        raise NotFound("Заявка не найдена")
    user = await get_target(session, actor, Perm.NAME_CHANGES_REVIEW, req.user_id)
    if req.status != RequestStatus.PENDING:
        raise Conflict("already_decided", "Заявка уже рассмотрена")
    reason = (reason or "").strip() or None
    old = {"first_name": user.first_name, "last_name": user.last_name}

    req.decided_at = now_utc()
    req.decided_by_id = actor.id
    req.reason = reason
    if approve:
        req.status = RequestStatus.APPROVED
        user.first_name, user.last_name = req.new_first_name, req.new_last_name
        text = f"ФИО изменено: {escape(user.last_name)} {escape(user.first_name)}"
    else:
        req.status = RequestStatus.REJECTED
        text = "Смена ФИО отклонена." + (f"\nПричина: {escape(reason)}" if reason else "")

    audit.record(
        session,
        category=Category.USERS,
        action="name_change.approved" if approve else "name_change.rejected",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="name_change_request",
        object_id=req.id,
        old=old,
        new={"first_name": user.first_name, "last_name": user.last_name},
        reason=reason,
    )
    await outbox.enqueue(
        session,
        dedup_key=f"name:{req.id}:decision",
        chat_id=user.telegram_id,
        related_user_id=user.id,
        text=text,
    )
    await session.commit()
    return req


# --- управление ---------------------------------------------------------------------


async def set_role(session: AsyncSession, actor: Actor, user_id: int, role: Role) -> User:
    actor.require_owner()
    if role == Role.OWNER:
        raise Forbidden("owner_role", "Роль владельца назначается только через OWNER_TELEGRAM_IDS")
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFound("Сотрудник не найден")
    if user.role == Role.OWNER:
        raise Forbidden("owner_role", "Роль владельца нельзя изменить из приложения")
    if user.status in (AccountStatus.UNREGISTERED, AccountStatus.PENDING, AccountStatus.REJECTED):
        raise Conflict("not_registered", "Сначала нужно одобрить регистрацию")
    if user.role == role:
        return user
    old = user.role
    user.role = role
    ev = audit.record(
        session,
        category=Category.USERS,
        action="role.changed",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="user",
        object_id=user.id,
        old={"role": old},
        new={"role": role},
    )
    await session.flush()
    await outbox.enqueue(
        session,
        dedup_key=f"audit:{ev.id}",
        chat_id=user.telegram_id,
        related_user_id=user.id,
        text=f"Ваша роль изменена: {ROLE_TITLES[role]}",
    )
    await session.commit()
    return user


MANAGEABLE_STATUSES = (AccountStatus.ACTIVE, AccountStatus.DISABLED, AccountStatus.BLOCKED)


async def set_status(
    session: AsyncSession, actor: Actor, user_id: int, status: AccountStatus, reason: str | None
) -> User:
    if status not in MANAGEABLE_STATUSES:
        raise AppError("bad_status", "Недопустимый статус")
    if user_id == actor.id:
        raise Forbidden("self", "Нельзя изменить статус собственного аккаунта")
    user = await get_target(session, actor, Perm.USERS_STATUS, user_id)
    await session.refresh(user, with_for_update=True)
    if user.role == Role.OWNER:
        raise Forbidden("owner", "Статус владельца нельзя изменить")
    if user.role == Role.ADMIN and not actor.is_owner:
        raise Forbidden("owner_only", "Статус администратора меняет только владелец")
    if user.status not in MANAGEABLE_STATUSES:
        raise Conflict("not_registered", "Сначала нужно рассмотреть заявку на регистрацию")
    if user.status == status:
        return user

    reason = (reason or "").strip() or None
    old = user.status
    user.status = status
    user.status_reason = reason
    ev = audit.record(
        session,
        category=Category.USERS,
        action="status.changed",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="user",
        object_id=user.id,
        old={"status": old},
        new={"status": status},
        reason=reason,
    )
    await session.flush()
    text = STATUS_MESSAGES.get(status, "Ваш аккаунт снова активен")
    await outbox.enqueue(
        session,
        dedup_key=f"audit:{ev.id}",
        chat_id=user.telegram_id,
        related_user_id=user.id,
        text=text + (f"\nПричина: {escape(reason)}" if reason else ""),
    )
    await session.commit()
    return user


async def set_app_access(session: AsyncSession, actor: Actor, user_id: int, enabled: bool) -> User:
    actor.require_owner()
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFound("Сотрудник не найден")
    if user.role == Role.OWNER:
        raise Forbidden("owner", "Доступ владельца нельзя закрыть")
    if user.app_access == enabled:
        return user
    user.app_access = enabled
    audit.record(
        session,
        category=Category.USERS,
        action="app_access.changed",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="user",
        object_id=user.id,
        old={"app_access": not enabled},
        new={"app_access": enabled},
    )
    await session.commit()
    return user


async def set_permissions(
    session: AsyncSession, actor: Actor, user_id: int, grants: set[tuple[Perm, int | None]]
) -> set[tuple[str, int | None]]:
    """Заменить набор разрешений человека. Изменения пишутся в аудит разницей."""
    actor.require_owner()
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFound("Сотрудник не найден")
    if user.role == Role.OWNER:
        raise Conflict("owner", "У владельца и так есть все права")
    if user.status != AccountStatus.ACTIVE:
        raise Conflict("not_active", "Разрешения выдаются только активным аккаунтам")

    network_ids = {n for _, n in grants if n is not None}
    if network_ids:
        found = set(
            (await session.execute(select(Network.id).where(Network.id.in_(network_ids)))).scalars()
        )
        if missing := network_ids - found:
            raise AppError("bad_network", f"Сеть не найдена: {sorted(missing)}")

    current = {
        (p, n)
        for p, n in (
            await session.execute(
                select(UserPermission.permission, UserPermission.network_id).where(
                    UserPermission.user_id == user.id
                )
            )
        )
    }
    wanted = {(p.value, n) for p, n in grants}
    removed = current - wanted
    added = wanted - current
    for p, n in removed:
        await session.execute(
            delete(UserPermission).where(
                UserPermission.user_id == user.id,
                UserPermission.permission == p,
                UserPermission.network_id.is_(None)
                if n is None
                else UserPermission.network_id == n,
            )
        )
    for p, n in added:
        session.add(
            UserPermission(user_id=user.id, permission=p, network_id=n, granted_by_id=actor.id)
        )
    if added or removed:
        audit.record(
            session,
            category=Category.USERS,
            action="permissions.changed",
            actor_id=actor.id,
            target_user_id=user.id,
            object_type="user",
            object_id=user.id,
            old=_sorted_grants(removed),
            new=_sorted_grants(added),
        )
    await session.commit()
    return wanted


def _sorted_grants(grants: set[tuple[str, int | None]]) -> list[list]:
    return [[p, n] for p, n in sorted(grants, key=lambda g: (g[0], g[1] or 0))]


async def visible_users_query(session: AsyncSession, actor: Actor):
    scope = await scope_for(session, actor, Perm.USERS_VIEW)
    q = select(User).where(User.status != AccountStatus.UNREGISTERED)
    if scope is not ALL:
        q = q.where(User.id.in_(scope))
    return q
