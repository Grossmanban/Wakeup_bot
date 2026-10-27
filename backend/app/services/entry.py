"""Скрытый режим бота: кто может войти и как (ТЗ «Скрытый режим, реферальная регистрация…»).

Сценарии при /start (проверяются на сервере, по порядку):
1. Уже в системе (зарегистрирован, заявка или анкета в работе, владелец) — обычный ответ.
2. Действительное приглашение действующего сотрудника (inv_<токен>) — регистрация данных
   без анкеты, отбора и стажировки.
3. Действительная реферальная ссылка (ref_<код>) — анкета кандидата и обычные этапы.
4. Ничего из этого — бот молчит, попытка записывается в журнал.

Тип приглашения определяется записью в базе по токену, а не текстом ссылки: подменить
«ref_» на «inv_» или перебрать токен нельзя (токен — 192 бита случайности).
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.config import get_settings
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import (
    AccessAttempt,
    AccessEvent,
    AccountStatus,
    Candidate,
    CandidateStatus,
    RegistrationRequest,
    RequestStatus,
    Role,
    StaffInvite,
    StaffInviteClaim,
    User,
    UserPermission,
)
from app.permissions import Perm
from app.security.telegram_auth import TelegramUser
from app.services import audit, outbox
from app.services import users as users_svc
from app.services.access import Actor
from app.services.audit import Category

STAFF_TEXT = (
    "Приглашение подтверждено. Откройте приложение и укажите фамилию и имя — "
    "после этого откроется рабочий интерфейс."
)
CANDIDATE_TEXT = (
    "Здравствуйте! Для начала работы необходимо заполнить регистрационную анкету. "
    "Нажмите кнопку ниже, чтобы продолжить."
)
ALREADY_TEXT = "Вы уже зарегистрированы. Откройте приложение, чтобы продолжить работу."

KNOWN_TEXT = {
    AccountStatus.PENDING: "Ваша заявка на рассмотрении. Мы сообщим о решении.",
    AccountStatus.REJECTED: "Ваша заявка отклонена.",
    AccountStatus.ACTIVE: "Откройте приложение, чтобы продолжить работу.",
    AccountStatus.UNREGISTERED: "Откройте приложение, чтобы продолжить.",
}

ATTEMPT_STATUS_TITLES = {
    "no_invite": "Нет приглашения",
    "allowed": "Доступ разрешён",
    "in_review": "На рассмотрении",
    "registered": "Зарегистрирован",
    "disabled": "Отключён",
    "blocked": "Заблокирован",
}

INVITE_STATUS_TITLES = {
    "active": "Действует",
    "used": "Использовано",
    "expired": "Истекло",
    "revoked": "Отозвано",
}
MAX_INVITE_DAYS = 30
EVENT_TEXT_LIMIT = 500


def _source(payload: str) -> str | None:
    """Параметр /start для журнала. Токен приглашения не сохраняется даже частично."""
    if not payload:
        return None
    return "inv_…" if payload.startswith("inv_") else payload


@dataclass
class StartReply:
    text: str
    show_app: bool


# --- журнал --------------------------------------------------------------------------


async def log_event(
    session: AsyncSession,
    tg: TelegramUser,
    kind: str,
    result: str,
    *,
    source: str | None = None,
    detail: dict[str, Any] | None = None,
    text: str | None = None,
) -> None:
    """Записать взаимодействие. Telegram ID — ключ: повторы не создают новых записей человека."""
    now = timeutil.now_utc()
    source = (source or None) and source[:80]
    stmt = insert(AccessAttempt).values(
        telegram_id=tg.id,
        username=tg.username,
        first_name=tg.first_name,
        last_name=tg.last_name,
        first_at=now,
        last_at=now,
        attempts=1,
        last_source=source,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["telegram_id"],
        set_={
            "username": func.coalesce(stmt.excluded.username, AccessAttempt.username),
            "first_name": func.coalesce(stmt.excluded.first_name, AccessAttempt.first_name),
            "last_name": func.coalesce(stmt.excluded.last_name, AccessAttempt.last_name),
            "last_at": now,
            "attempts": AccessAttempt.attempts + 1,
            "last_source": func.coalesce(stmt.excluded.last_source, AccessAttempt.last_source),
        },
    )
    await session.execute(stmt)
    session.add(
        AccessEvent(
            telegram_id=tg.id,
            at=now,
            kind=kind,
            result=result,
            source=source,
            detail=detail,
            text=(text or None) and text[:EVENT_TEXT_LIMIT],
        )
    )


# --- проверки ------------------------------------------------------------------------


def invite_status(inv: StaffInvite, now: datetime | None = None) -> str:
    now = now or timeutil.now_utc()
    if inv.revoked_at is not None:
        return "revoked"
    if inv.expires_at is not None and inv.expires_at <= now:
        return "expired"
    if inv.uses >= inv.max_uses:
        return "used"
    return "active"


async def valid_referrer(session: AsyncSession, code: str) -> User | None:
    """Ссылка действительна, только пока её владелец активен и имеет право на рефералов."""
    if not code:
        return None
    ref = await session.scalar(select(User).where(User.referral_code == code.lower()))
    if ref is None or ref.status != AccountStatus.ACTIVE:
        return None
    if ref.role == Role.OWNER:
        return ref
    granted = await session.scalar(
        select(UserPermission.id).where(
            UserPermission.user_id == ref.id, UserPermission.permission == Perm.REFERRALS_USE.value
        )
    )
    return ref if granted else None


async def referral_allowed(session: AsyncSession, user: User) -> bool:
    if user.referred_by_id is None:
        return False
    ref = await session.get(User, user.referred_by_id)
    return (
        ref is not None
        and ref.referral_code is not None
        and (await valid_referrer(session, ref.referral_code)) is not None
    )


async def active_claim(
    session: AsyncSession, telegram_id: int, *, lock: bool = False
) -> tuple[StaffInviteClaim, StaffInvite] | None:
    """Последнее неиспользованное приглашение этого человека, которое ещё действует."""
    rows = (
        await session.execute(
            select(StaffInviteClaim, StaffInvite)
            .join(StaffInvite, StaffInvite.id == StaffInviteClaim.invite_id)
            .where(StaffInviteClaim.telegram_id == telegram_id, StaffInviteClaim.used_at.is_(None))
            .order_by(StaffInviteClaim.id.desc())
        )
    ).all()
    for claim, inv in rows:
        if invite_status(inv) == "active":
            if lock:
                inv = await session.get(
                    StaffInvite, inv.id, with_for_update=True, populate_existing=True
                )
                if invite_status(inv) != "active":
                    continue
            return claim, inv
    return None


async def _open_candidate(session: AsyncSession, user: User) -> Candidate | None:
    return await session.scalar(
        select(Candidate)
        .where(Candidate.user_id == user.id, Candidate.status != CandidateStatus.REJECTED)
        .order_by(Candidate.id.desc())
        .limit(1)
    )


async def is_known(session: AsyncSession, user: User | None, telegram_id: int) -> bool:
    """В системе: владелец, есть аккаунт (кроме «не зарегистрирован») или анкета в работе."""
    if telegram_id in get_settings().owner_telegram_ids:
        return True
    if user is None:
        return False
    if user.status != AccountStatus.UNREGISTERED:
        return True
    return await _open_candidate(session, user) is not None


# --- /start --------------------------------------------------------------------------


async def handle_start(session: AsyncSession, tg: TelegramUser, payload: str) -> StartReply | None:
    """Решить, что ответить на /start. None — промолчать (попытка уже записана)."""
    from app.services import candidates as candidates_svc

    payload = (payload or "").strip()
    user = await session.scalar(select(User).where(User.telegram_id == tg.id))

    if await is_known(session, user, tg.id):
        user = await users_svc.upsert_from_telegram(session, tg, started_bot=True)
        await log_event(session, tg, "start", "known", source=_source(payload))
        await session.commit()
        if user.status in users_svc.STATUS_MESSAGES:
            return StartReply(users_svc.STATUS_MESSAGES[user.status], False)
        if payload.startswith("inv_") and user.status == AccountStatus.ACTIVE:
            # Приглашение не используется и роли не меняются: человек уже в системе.
            return StartReply(ALREADY_TEXT, True)
        return StartReply(KNOWN_TEXT[user.status], True)

    if payload.startswith("inv_"):
        inv = await _claim_invite(session, payload[4:], tg)
        if inv is not None:
            await users_svc.upsert_from_telegram(session, tg, started_bot=True)
            await log_event(
                session,
                tg,
                "invite_claimed",
                "allowed",
                source=_source(payload),
                detail={"invite_id": inv.id},
            )
            await session.commit()
            return StartReply(STAFF_TEXT, True)

    if payload.startswith("ref_"):
        ref = await valid_referrer(session, payload[4:])
        if ref is not None:
            user = await users_svc.upsert_from_telegram(session, tg, started_bot=True)
            await log_event(
                session, tg, "referral", "allowed", source=payload, detail={"referrer_id": ref.id}
            )
            await session.commit()
            await candidates_svc.record_visit(session, user, payload[4:])
            return StartReply(CANDIDATE_TEXT, True)

    # Разрешение, полученное раньше (повторный /start без параметров).
    if user is not None:
        if await active_claim(session, tg.id):
            await log_event(session, tg, "start", "allowed", source=_source(payload))
            await session.commit()
            return StartReply(STAFF_TEXT, True)
        if await referral_allowed(session, user):
            await log_event(session, tg, "start", "allowed", source=_source(payload))
            await session.commit()
            return StartReply(CANDIDATE_TEXT, True)

    await log_event(session, tg, "start", "ignored", source=_source(payload))
    await session.commit()
    return None


async def note_interaction(
    session: AsyncSession, tg: TelegramUser, kind: str, text: str | None
) -> bool:
    """Сообщение или кнопка в боте. True — человек в системе и действие можно обработать."""
    user = await session.scalar(select(User).where(User.telegram_id == tg.id))
    if await is_known(session, user, tg.id):
        return True
    await log_event(session, tg, kind, "ignored", text=text)
    await session.commit()
    return False


async def _claim_invite(session: AsyncSession, token: str, tg: TelegramUser) -> StaffInvite | None:
    if not token or len(token) > 64:
        return None
    inv = await session.scalar(
        select(StaffInvite).where(StaffInvite.token == token).with_for_update()
    )
    if inv is None or invite_status(inv) != "active":
        return None
    exists = await session.scalar(
        select(StaffInviteClaim.id).where(
            StaffInviteClaim.invite_id == inv.id, StaffInviteClaim.telegram_id == tg.id
        )
    )
    if not exists:
        session.add(StaffInviteClaim(invite_id=inv.id, telegram_id=tg.id))
    return inv


# --- Mini App ------------------------------------------------------------------------


async def app_screen(session: AsyncSession, user: User) -> str:
    """Экран для ещё не зарегистрированного: owner_name | staff_register | candidate | no_access."""
    if user.role == Role.OWNER:
        return "owner_name"
    if await active_claim(session, user.telegram_id):
        return "staff_register"
    if await _open_candidate(session, user) is not None or await referral_allowed(session, user):
        return "candidate"
    return "no_access"


async def note_app_open(session: AsyncSession, user: User) -> None:
    """Постороннего, открывшего приложение напрямую, видно в журнале (не чаще раза в 10 минут)."""
    last = await session.scalar(
        select(AccessEvent.at)
        .where(AccessEvent.telegram_id == user.telegram_id, AccessEvent.kind == "app")
        .order_by(AccessEvent.id.desc())
        .limit(1)
    )
    if last is not None and timeutil.now_utc() - last < timedelta(minutes=10):
        return
    tg = TelegramUser(user.telegram_id, user.tg_first_name or "", user.tg_last_name, user.username)
    await log_event(session, tg, "app", "ignored")
    await session.commit()


async def register_staff(session: AsyncSession, user: User, first: str, last: str) -> User:
    """Регистрация действующего сотрудника по приглашению: только данные, сразу в работу."""
    if user.status == AccountStatus.ACTIVE:
        return user  # уже в системе: без дубликата и без изменения ролей
    if user.status in users_svc.STATUS_MESSAGES:
        raise Forbidden(f"account_{user.status.value}", users_svc.STATUS_MESSAGES[user.status])
    first = users_svc.clean_name(first, "имя")
    last = users_svc.clean_name(last, "фамилию")
    found = await active_claim(session, user.telegram_id, lock=True)
    if found is None:
        raise Forbidden("invite_required", "Регистрация только по действующему приглашению")
    claim, inv = found
    user = await session.get(User, user.id, with_for_update=True, populate_existing=True)
    if user.status == AccountStatus.ACTIVE:
        return user

    old = {"status": user.status, "role": user.role}
    user.first_name, user.last_name = first, last
    user.status = AccountStatus.ACTIVE
    if user.role != Role.EMPLOYEE:
        user.role = Role.EMPLOYEE  # приглашение не даёт прав выше сотрудника
    inv.uses += 1
    claim.used_at = timeutil.now_utc()
    claim.user_id = user.id
    # Открытая заявка на регистрацию больше не нужна.
    await session.execute(
        update(RegistrationRequest)
        .where(
            RegistrationRequest.user_id == user.id,
            RegistrationRequest.status == RequestStatus.PENDING,
        )
        .values(
            status=RequestStatus.CANCELLED,
            reason="Зарегистрирован по приглашению действующего сотрудника",
            decided_at=timeutil.now_utc(),
        )
    )
    audit.record(
        session,
        category=Category.USERS,
        action="staff_invite.used",
        actor_id=user.id,
        target_user_id=user.id,
        object_type="staff_invite",
        object_id=inv.id,
        old=old,
        new={"status": user.status, "role": user.role, "name": f"{last} {first}"},
        reason="Регистрация действующего сотрудника по приглашению",
    )
    tg = TelegramUser(user.telegram_id, user.tg_first_name or "", user.tg_last_name, user.username)
    await log_event(session, tg, "invite_used", "allowed", detail={"invite_id": inv.id})
    inviter = await session.get(User, inv.created_by_id)
    if inviter is not None:
        await outbox.enqueue(
            session,
            dedup_key=f"staff_invite:{inv.id}:used:{user.id}",
            chat_id=inviter.telegram_id,
            text=f"По вашему приглашению зарегистрировался сотрудник: {escape(user.full_name)}",
            related_user_id=inviter.id,
        )
    await session.commit()
    return user


# --- администрирование ---------------------------------------------------------------


def invite_link(inv: StaffInvite) -> str | None:
    bot = get_settings().bot_username
    return f"https://t.me/{bot}?start=inv_{inv.token}" if bot else None


async def create_invite(
    session: AsyncSession,
    actor: Actor,
    *,
    expires_in_hours: int | None,
    max_uses: int,
    note: str | None,
) -> StaffInvite:
    actor.require(Perm.STAFF_INVITES)
    if not 1 <= max_uses <= 50:
        raise AppError("bad_max_uses", "Число использований — от 1 до 50")
    if expires_in_hours is not None and not 1 <= expires_in_hours <= MAX_INVITE_DAYS * 24:
        raise AppError("bad_expiry", f"Срок действия — от 1 часа до {MAX_INVITE_DAYS} дней")
    if not get_settings().bot_username:
        raise Conflict("no_bot_username", "Не задано имя бота (BOT_USERNAME) — ссылку не собрать")
    inv = StaffInvite(
        token=secrets.token_urlsafe(24),
        kind="staff",
        created_by_id=actor.id,
        note=(note or "").strip()[:200] or None,
        expires_at=(
            timeutil.now_utc() + timedelta(hours=expires_in_hours) if expires_in_hours else None
        ),
        max_uses=max_uses,
    )
    session.add(inv)
    await session.flush()
    audit.record(
        session,
        category=Category.USERS,
        action="staff_invite.created",
        actor_id=actor.id,
        object_type="staff_invite",
        object_id=inv.id,
        new={"expires_at": inv.expires_at, "max_uses": max_uses, "note": inv.note},
    )
    await session.commit()
    return inv


async def _invite_for(session: AsyncSession, actor: Actor, invite_id: int) -> StaffInvite:
    actor.require(Perm.STAFF_INVITES)
    inv = await session.get(StaffInvite, invite_id, with_for_update=True)
    # Владелец видит все приглашения, остальные — только свои.
    if inv is None or (not actor.is_owner and inv.created_by_id != actor.id):
        raise NotFound("Приглашение не найдено")
    return inv


async def revoke_invite(session: AsyncSession, actor: Actor, invite_id: int) -> StaffInvite:
    inv = await _invite_for(session, actor, invite_id)
    if inv.revoked_at is None:
        inv.revoked_at = timeutil.now_utc()
        inv.revoked_by_id = actor.id
        audit.record(
            session,
            category=Category.USERS,
            action="staff_invite.revoked",
            actor_id=actor.id,
            object_type="staff_invite",
            object_id=inv.id,
        )
        await session.commit()
    return inv


async def list_invites(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    actor.require(Perm.STAFF_INVITES)
    q = select(StaffInvite).order_by(StaffInvite.id.desc()).limit(200)
    if not actor.is_owner:
        q = q.where(StaffInvite.created_by_id == actor.id)
    invites = (await session.execute(q)).scalars().all()
    ids = [i.id for i in invites]
    claims: dict[int, list[dict[str, Any]]] = {}
    if ids:
        rows = await session.execute(
            select(StaffInviteClaim, User)
            .outerjoin(User, User.id == StaffInviteClaim.user_id)
            .where(StaffInviteClaim.invite_id.in_(ids))
            .order_by(StaffInviteClaim.id)
        )
        for c, u in rows:
            claims.setdefault(c.invite_id, []).append(
                {
                    "telegram_id": c.telegram_id,
                    "claimed_at": c.claimed_at,
                    "used_at": c.used_at,
                    "user_id": c.user_id,
                    "full_name": u.full_name if u else None,
                }
            )
    authors = (
        {
            u.id: u.full_name
            for u in (
                await session.execute(
                    select(User).where(User.id.in_({i.created_by_id for i in invites}))
                )
            ).scalars()
        }
        if invites
        else {}
    )
    return [
        invite_out(i, claims.get(i.id, []), authors.get(i.created_by_id), actor) for i in invites
    ]


def invite_out(
    inv: StaffInvite, claims: list[dict[str, Any]], author: str | None, actor: Actor
) -> dict[str, Any]:
    status = invite_status(inv)
    return {
        "id": inv.id,
        "kind": inv.kind,
        "kind_title": "Действующий сотрудник",
        "note": inv.note,
        "created_at": inv.created_at,
        "created_by": author,
        "expires_at": inv.expires_at,
        "max_uses": inv.max_uses,
        "uses": inv.uses,
        "status": status,
        "status_title": INVITE_STATUS_TITLES[status],
        "revoked_at": inv.revoked_at,
        # Ссылку можно скопировать, пока приглашение действует.
        "link": invite_link(inv) if status == "active" else None,
        "claims": claims,
    }


async def _attempt_statuses(session: AsyncSession, tg_ids: list[int]) -> dict[int, str]:
    users = {
        u.telegram_id: u
        for u in (await session.execute(select(User).where(User.telegram_id.in_(tg_ids)))).scalars()
    }
    out: dict[int, str] = {}
    for tg in tg_ids:
        u = users.get(tg)
        if u is not None and u.status == AccountStatus.ACTIVE:
            out[tg] = "registered"
        elif u is not None and u.status == AccountStatus.DISABLED:
            out[tg] = "disabled"
        elif u is not None and u.status == AccountStatus.BLOCKED:
            out[tg] = "blocked"
        elif u is not None and (
            u.status == AccountStatus.PENDING or await _open_candidate(session, u) is not None
        ):
            out[tg] = "in_review"
        elif await active_claim(session, tg) or (
            u is not None and await referral_allowed(session, u)
        ):
            out[tg] = "allowed"
        else:
            out[tg] = "no_invite"
    return out


def _attempt_out(a: AccessAttempt, status: str, user: User | None) -> dict[str, Any]:
    return {
        "telegram_id": a.telegram_id,
        "username": a.username,
        "first_name": a.first_name,
        "last_name": a.last_name,
        "first_at": a.first_at,
        "last_at": a.last_at,
        "attempts": a.attempts,
        "source": a.last_source,
        "status": status,
        "status_title": ATTEMPT_STATUS_TITLES[status],
        "user_id": user.id if user else None,
    }


async def list_attempts(
    session: AsyncSession, actor: Actor, status: str | None, q: str | None
) -> list[dict[str, Any]]:
    actor.require(Perm.ACCESS_LOG_VIEW)
    query = select(AccessAttempt).order_by(AccessAttempt.last_at.desc()).limit(500)
    if q:
        like = f"%{q.strip().lstrip('@')}%"
        conds = [
            AccessAttempt.username.ilike(like),
            AccessAttempt.first_name.ilike(like),
            AccessAttempt.last_name.ilike(like),
        ]
        if q.strip().isdigit():
            conds.append(AccessAttempt.telegram_id == int(q.strip()))
        query = query.where(or_(*conds))
    attempts = (await session.execute(query)).scalars().all()
    statuses = await _attempt_statuses(session, [a.telegram_id for a in attempts])
    users = {
        u.telegram_id: u
        for u in (
            await session.execute(
                select(User).where(User.telegram_id.in_([a.telegram_id for a in attempts] or [0]))
            )
        ).scalars()
    }
    out = [_attempt_out(a, statuses[a.telegram_id], users.get(a.telegram_id)) for a in attempts]
    return [r for r in out if not status or r["status"] == status]


async def attempt_card(session: AsyncSession, actor: Actor, telegram_id: int) -> dict[str, Any]:
    actor.require(Perm.ACCESS_LOG_VIEW)
    a = await session.get(AccessAttempt, telegram_id)
    if a is None:
        raise NotFound("Записей по этому Telegram ID нет")
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    status = (await _attempt_statuses(session, [telegram_id]))[telegram_id]
    events = (
        (
            await session.execute(
                select(AccessEvent)
                .where(AccessEvent.telegram_id == telegram_id)
                .order_by(AccessEvent.at, AccessEvent.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        **_attempt_out(a, status, user),
        "events": [
            {
                "at": e.at,
                "kind": e.kind,
                "result": e.result,
                "source": e.source,
                "text": e.text,
                "detail": e.detail,
            }
            for e in events
        ],
    }
