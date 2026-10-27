"""Рефералы, анкеты кандидатов, обучение и учебные единицы (ТЗ 12)."""

import io
import secrets
from datetime import datetime
from html import escape
from typing import Any

import segno
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.config import get_settings
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import (
    DONE_STATUSES,
    AccountStatus,
    Candidate,
    CandidateEvent,
    CandidateStatus,
    EntryKind,
    LedgerEntry,
    Product,
    ProductAccess,
    ProductStatus,
    ReferralEvent,
    RegistrationRequest,
    RequestStatus,
    Task,
    TaskStatus,
    TelegramOutbox,
    TrainingConfirmation,
    TrainingSession,
    User,
)
from app.money import rub
from app.permissions import Perm
from app.services import audit, notify, outbox
from app.services import settings as settings_svc
from app.services.access import Actor
from app.services.audit import Category
from app.services.users import clean_name

OPEN = (
    CandidateStatus.DRAFT,
    CandidateStatus.SUBMITTED,
    CandidateStatus.REVIEWING,
    CandidateStatus.NEEDS_INFO,
    CandidateStatus.APPROVED,
)


# --- рефералы -----------------------------------------------------------------------


async def referral_link(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    actor.require(Perm.REFERRALS_USE)
    user = await session.get(User, actor.id, with_for_update=True)
    if not user.referral_code:
        for _ in range(10):
            code = secrets.token_urlsafe(6).replace("-", "").replace("_", "").lower()[:8]
            if len(code) == 8 and not await session.scalar(
                select(User.id).where(User.referral_code == code)
            ):
                user.referral_code = code
                break
        await session.commit()
    bot = get_settings().bot_username
    if not bot:
        raise Conflict(
            "no_bot_username", "Не задано имя бота (BOT_USERNAME) — ссылку сформировать нельзя"
        )
    return {
        "code": user.referral_code,
        "link": f"https://t.me/{bot}?start=ref_{user.referral_code}",
    }


def qr_svg(link: str) -> bytes:
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="svg", scale=6, border=2)
    return buf.getvalue()


async def _ref_event(session: AsyncSession, referrer_id: int | None, kind: str, **kw) -> None:
    if referrer_id is None:
        return
    session.add(ReferralEvent(referrer_id=referrer_id, kind=kind, **kw))


async def record_visit(session: AsyncSession, user: User, code: str) -> None:
    """Переход по ссылке: учитывается только реально полученный ботом /start."""
    referrer = await session.scalar(select(User).where(User.referral_code == code.lower()))
    if referrer is None or referrer.id == user.id:
        return
    seen = await session.scalar(
        select(ReferralEvent.id).where(
            ReferralEvent.referrer_id == referrer.id,
            ReferralEvent.kind == "visit",
            ReferralEvent.telegram_id == user.telegram_id,
        )
    )
    if not seen:
        session.add(
            ReferralEvent(referrer_id=referrer.id, kind="visit", telegram_id=user.telegram_id)
        )
    # Привязка к пригласившему — только для тех, кто ещё не в системе.
    if user.referred_by_id is None and user.status == AccountStatus.UNREGISTERED:
        user.referred_by_id = referrer.id
    await session.commit()


async def referral_stats(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    actor.require(Perm.REFERRALS_USE)
    counts = dict(
        (
            await session.execute(
                select(ReferralEvent.kind, func.count())
                .where(ReferralEvent.referrer_id == actor.id)
                .group_by(ReferralEvent.kind)
            )
        ).all()
    )
    rows = (
        await session.execute(
            select(Candidate, User)
            .join(User, User.id == Candidate.user_id)
            .where(Candidate.referrer_id == actor.id)
            .order_by(Candidate.id.desc())
        )
    ).all()
    bonuses = (
        (
            await session.execute(
                select(LedgerEntry).where(
                    LedgerEntry.user_id == actor.id, LedgerEntry.kind == EntryKind.REFERRAL_BONUS
                )
            )
        )
        .scalars()
        .all()
    )
    reversed_kop = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_kop), 0)).where(
            LedgerEntry.kind == EntryKind.BONUS_REVERSAL,
            LedgerEntry.source_entry_id.in_([b.id for b in bonuses] or [0]),
        )
    )
    trainees = {u.id: u for _, u in rows}
    return {
        "visits": counts.get("visit", 0),
        "forms_started": counts.get("form_started", 0),
        "submitted": counts.get("submitted", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "training": counts.get("training", 0),
        "training_done": counts.get("training_done", 0),
        "bonus_kop": sum(b.amount_kop for b in bonuses) + int(reversed_kop),
        "candidates": [
            {
                "id": c.id,
                "name": " ".join(x for x in (c.last_name, c.first_name) if x) or u.full_name,
                "status": c.status.value,
                "training_status": trainees[u.id].training_status,
                "submitted_at": c.submitted_at,
            }
            for c, u in rows
        ],
    }


# --- анкета -------------------------------------------------------------------------


def candidate_out(c: Candidate) -> dict[str, Any]:
    return {
        k: getattr(c, k)
        for k in (
            "id",
            "status",
            "first_name",
            "last_name",
            "location",
            "days_per_week",
            "can_train",
            "skills",
            "truthful",
            "pd_consent_version",
            "pd_consent_at",
            "submitted_at",
            "reason",
            "info_request",
            "contact_status",
            "contact_reported_at",
            "responsible_id",
            "referrer_id",
            "created_at",
            "decided_at",
        )
    } | {"status": c.status.value}


async def my_candidate(session: AsyncSession, user: User) -> Candidate | None:
    return await session.scalar(
        select(Candidate).where(Candidate.user_id == user.id).order_by(Candidate.id.desc()).limit(1)
    )


async def _event(
    session: AsyncSession, c: Candidate, kind: str, actor_id: int | None, **data
) -> CandidateEvent:
    ev = CandidateEvent(candidate_id=c.id, actor_id=actor_id, kind=kind, data=data)
    session.add(ev)
    await session.flush()
    return ev


async def save_form(session: AsyncSession, user: User, data: dict[str, Any]) -> Candidate:
    """Черновик анкеты. Менять можно в статусах «черновик» и «требуется уточнение»."""
    if user.status not in (AccountStatus.UNREGISTERED, AccountStatus.REJECTED):
        raise Conflict("already_user", "Вы уже зарегистрированы в системе")
    c = await session.scalar(
        select(Candidate)
        .where(Candidate.user_id == user.id, Candidate.status.in_(OPEN))
        .with_for_update()
    )
    if c is None:
        from app.services import entry

        # Новая анкета — только по действительной реферальной ссылке (скрытый режим бота).
        if not await entry.referral_allowed(session, user):
            raise Forbidden("referral_required", "Анкета доступна только по приглашению")
        c = Candidate(
            user_id=user.id, referrer_id=user.referred_by_id, status=CandidateStatus.DRAFT
        )
        session.add(c)
        await session.flush()
        await _event(session, c, "created", user.id)
        await _ref_event(
            session, c.referrer_id, "form_started", candidate_id=c.id, telegram_id=user.telegram_id
        )
    elif c.status not in (CandidateStatus.DRAFT, CandidateStatus.NEEDS_INFO):
        raise Conflict("submitted", "Анкета уже отправлена")
    for k in ("first_name", "last_name"):
        if data.get(k) is not None:
            setattr(c, k, clean_name(data[k], "имя" if k == "first_name" else "фамилию"))
    if data.get("location") is not None:
        c.location = " ".join(data["location"].split())[:200] or None
    if data.get("days_per_week") is not None:
        if not 1 <= data["days_per_week"] <= 7:
            raise AppError("bad_days", "Дней в неделю — от 1 до 7")
        c.days_per_week = data["days_per_week"]
    if data.get("can_train") is not None:
        c.can_train = data["can_train"]
    if data.get("skills") is not None:
        allowed = await settings_svc.get(session, "candidate_skills")
        skills = {k: int(v) for k, v in data["skills"].items() if k in allowed}
        if any(not 1 <= v <= 10 for v in skills.values()):
            raise AppError("bad_skill", "Оценка навыка — от 1 до 10")
        c.skills = skills
    if data.get("truthful") is not None:
        c.truthful = bool(data["truthful"])
    if data.get("pd_consent"):
        c.pd_consent_version = await settings_svc.get(session, "pd_consent_version")
        c.pd_consent_at = timeutil.now_utc()
    await session.commit()
    return c


async def submit(session: AsyncSession, user: User) -> Candidate:
    c = await session.scalar(
        select(Candidate)
        .where(Candidate.user_id == user.id, Candidate.status.in_(OPEN))
        .with_for_update()
    )
    if c is None:
        raise NotFound("Анкета не найдена")
    if c.status not in (CandidateStatus.DRAFT, CandidateStatus.NEEDS_INFO):
        return c
    skills = await settings_svc.get(session, "candidate_skills")
    missing = []
    if not (c.first_name and c.last_name):
        missing.append("фамилия и имя")
    if not c.location:
        missing.append("метро или район")
    if not c.days_per_week:
        missing.append("дни в неделю")
    if c.can_train is None:
        missing.append("возможность пройти обучение")
    if set(skills) - set(c.skills):
        missing.append("оценка навыков")
    if not c.truthful:
        missing.append("подтверждение достоверности")
    if not c.pd_consent_at:
        missing.append("согласие на обработку персональных данных")
    if missing:
        raise AppError("incomplete", "Заполните: " + ", ".join(missing))
    was = c.status
    c.status = CandidateStatus.SUBMITTED
    c.submitted_at = timeutil.now_utc()
    c.info_request = None
    ev = await _event(
        session, c, "submitted", user.id, resubmitted=was == CandidateStatus.NEEDS_INFO
    )
    if was == CandidateStatus.DRAFT:
        await _ref_event(session, c.referrer_id, "submitted", candidate_id=c.id)
    await notify.notify_holders(
        session,
        Perm.CANDIDATES_REVIEW,
        dedup_key=f"candidate_event:{ev.id}",
        text=(
            "<b>Анкета кандидата</b>\n"
            f"{escape(c.last_name)} {escape(c.first_name)}, {escape(c.location)}"
        ),
        button="Рассмотреть",
    )
    await session.commit()
    return c


async def report_contact(session: AsyncSession, user: User, status: str) -> Candidate:
    """Кнопки кандидата «Я написал» / «Не могу связаться» (ТЗ 12.3)."""
    if status not in ("wrote", "cannot_contact"):
        raise AppError("bad_status", "Недопустимое значение")
    c = await my_candidate(session, user)
    if c is None or c.status not in (
        CandidateStatus.APPROVED,
        CandidateStatus.REVIEWING,
        CandidateStatus.SUBMITTED,
    ):
        raise Conflict("bad_state", "Сейчас это действие недоступно")
    c.contact_status = status
    c.contact_reported_at = timeutil.now_utc()
    ev = await _event(session, c, f"contact_{status}", user.id)
    text = f"Кандидат {escape(c.last_name or '')} {escape(c.first_name or '')} " + (
        "сообщил, что написал вам" if status == "wrote" else "не может связаться с ответственным"
    )
    if c.responsible_id:
        resp = await session.get(User, c.responsible_id)
        await outbox.enqueue(
            session,
            dedup_key=f"candidate_event:{ev.id}",
            chat_id=resp.telegram_id,
            related_user_id=resp.id,
            text=text,
        )
    else:
        await notify.notify_holders(
            session, Perm.CANDIDATES_REVIEW, dedup_key=f"candidate_event:{ev.id}", text=text
        )
    await session.commit()
    return c


# --- рассмотрение -------------------------------------------------------------------


async def _candidate_for_review(session: AsyncSession, actor: Actor, cid: int) -> Candidate:
    actor.require(Perm.CANDIDATES_REVIEW)
    c = await session.get(Candidate, cid, with_for_update=True)
    if c is None:
        raise NotFound("Анкета не найдена")
    return c


async def _tell_candidate(
    session: AsyncSession, c: Candidate, key: str, text: str, button: str | None = "Открыть"
) -> None:
    u = await session.get(User, c.user_id)
    await outbox.enqueue(
        session,
        dedup_key=key,
        chat_id=u.telegram_id,
        related_user_id=u.id,
        text=text,
        open_app_button=button,
    )


async def review_action(
    session: AsyncSession, actor: Actor, cid: int, action: str, text: str | None
) -> Candidate:
    """take | approve | reject | request_info | comment."""
    c = await _candidate_for_review(session, actor, cid)
    text = (text or "").strip() or None
    reviewable = (CandidateStatus.SUBMITTED, CandidateStatus.REVIEWING)
    old = c.status
    if action == "take":
        if c.status != CandidateStatus.SUBMITTED:
            raise Conflict("bad_state", "Анкета уже рассматривается")
        c.status = CandidateStatus.REVIEWING
        c.responsible_id = c.responsible_id or actor.id
    elif action == "approve":
        if c.status not in reviewable:
            raise Conflict("bad_state", "Одобрить можно отправленную анкету")
        c.status = CandidateStatus.APPROVED
        await _ref_event(session, c.referrer_id, "approved", candidate_id=c.id)
    elif action == "reject":
        if c.status not in (*reviewable, CandidateStatus.NEEDS_INFO, CandidateStatus.APPROVED):
            raise Conflict("bad_state", "Анкету нельзя отклонить")
        if not text:
            raise AppError("reason_required", "Укажите причину отказа")
        c.status = CandidateStatus.REJECTED
        c.reason = text
        await _ref_event(session, c.referrer_id, "rejected", candidate_id=c.id)
    elif action == "request_info":
        if c.status not in reviewable:
            raise Conflict("bad_state", "Уточнение можно запросить по отправленной анкете")
        if not text:
            raise AppError("reason_required", "Напишите, что нужно уточнить")
        c.status = CandidateStatus.NEEDS_INFO
        c.info_request = text
    elif action == "comment":
        if not text:
            raise AppError("reason_required", "Пустой комментарий")
        c.comment = text
    else:
        raise AppError("bad_action", "Неизвестное действие")
    if action in ("approve", "reject"):
        c.decided_at = timeutil.now_utc()
        c.decided_by_id = actor.id
    ev = await _event(session, c, action, actor.id, text=text, previous=old.value)
    audit.record(
        session,
        category=Category.USERS,
        action=f"candidate.{action}",
        actor_id=actor.id,
        target_user_id=c.user_id,
        object_type="candidate",
        object_id=c.id,
        old={"status": old},
        new={"status": c.status},
        reason=text,
    )
    messages = {
        "approve": "Ваша анкета одобрена. С вами свяжется ответственный.",
        "reject": f"Ваша анкета отклонена.\nПричина: {escape(text or '')}",
        "request_info": f"По вашей анкете нужно уточнение:\n{escape(text or '')}",
    }
    if action in messages:
        await _tell_candidate(session, c, f"candidate_event:{ev.id}", messages[action])
    await session.commit()
    return c


async def assign_responsible(
    session: AsyncSession, actor: Actor, cid: int, responsible_id: int, send_template: bool
) -> dict[str, Any]:
    """Выбрать ответственного и (по желанию) отправить кандидату шаблонное сообщение."""
    c = await _candidate_for_review(session, actor, cid)
    resp = await session.get(User, responsible_id)
    if resp is None or resp.status != AccountStatus.ACTIVE:
        raise NotFound("Сотрудник не найден")
    c.responsible_id = resp.id
    ev = await _event(session, c, "responsible", actor.id, responsible_id=resp.id)
    key = None
    if send_template:
        template = await settings_svc.get(session, "candidate_contact_template")
        contact = f"@{resp.username}" if resp.username else f"Telegram ID {resp.telegram_id}"
        try:
            text = template.format(
                name=c.first_name or "", responsible=resp.full_name, contact=contact
            )
        except (KeyError, IndexError, ValueError) as e:
            raise AppError(
                "bad_template",
                "Ошибка в шаблоне сообщения: проверьте {name}, {responsible}, {contact}",
            ) from e
        key = f"candidate_event:{ev.id}:template"
        await _tell_candidate(session, c, key, escape(text), None)
        c.contact_message_sent_at = timeutil.now_utc()
    await session.commit()
    return {"candidate": candidate_out(c), "message_key": key}


async def delivery_status(session: AsyncSession, key: str | None) -> str | None:
    """Статус личного сообщения: «отправлено» — только если Telegram подтвердил отправку."""
    if not key:
        return None
    st = await session.scalar(select(TelegramOutbox.status).where(TelegramOutbox.dedup_key == key))
    return st.value if st else None


async def list_candidates(
    session: AsyncSession, actor: Actor, status: str | None
) -> list[dict[str, Any]]:
    actor.require(Perm.CANDIDATES_REVIEW)
    q = (
        select(Candidate, User)
        .join(User, User.id == Candidate.user_id)
        .order_by(Candidate.id.desc())
        .limit(300)
    )
    if status:
        q = q.where(Candidate.status == CandidateStatus(status))
    else:
        q = q.where(Candidate.status != CandidateStatus.DRAFT)
    out = []
    for c, u in await session.execute(q):
        out.append({**candidate_out(c), "username": u.username, "telegram_id": u.telegram_id})
    return out


async def candidate_details(session: AsyncSession, actor: Actor, cid: int) -> dict[str, Any]:
    if not (actor.has(Perm.CANDIDATES_REVIEW) or actor.has(Perm.TRAINING_MANAGE)):
        raise Forbidden("no_permission", "Недостаточно прав для этого действия")
    c = await session.get(Candidate, cid)
    if c is None:
        raise NotFound("Анкета не найдена")
    u = await session.get(User, c.user_id)
    events = (
        (
            await session.execute(
                select(CandidateEvent)
                .where(CandidateEvent.candidate_id == c.id)
                .order_by(CandidateEvent.id)
            )
        )
        .scalars()
        .all()
    )
    sessions = (
        (
            await session.execute(
                select(TrainingSession)
                .where(TrainingSession.candidate_id == c.id)
                .order_by(TrainingSession.id)
            )
        )
        .scalars()
        .all()
    )
    tmpl = next((e for e in reversed(events) if e.kind == "responsible"), None)
    referrer = await session.get(User, c.referrer_id) if c.referrer_id else None
    responsible = await session.get(User, c.responsible_id) if c.responsible_id else None
    return {
        **candidate_out(c),
        "username": u.username,
        "telegram_id": u.telegram_id,
        "user_id": u.id,
        "can_message": u.can_message,
        "referrer": referrer.full_name if referrer else None,
        "responsible": responsible.full_name if responsible else None,
        "template_status": await delivery_status(
            session, f"candidate_event:{tmpl.id}:template" if tmpl else None
        ),
        "events": [{"kind": e.kind, "data": e.data, "created_at": e.created_at} for e in events],
        "trainings": [training_out(t) for t in sessions],
        "training_progress": await training_progress(session, u.id)
        if u.training_status != "none"
        else None,
    }


# --- обучение -----------------------------------------------------------------------


def training_out(t: TrainingSession) -> dict[str, Any]:
    return {
        k: getattr(t, k)
        for k in (
            "id",
            "starts_at",
            "place",
            "contact_user_id",
            "comment",
            "status",
            "confirmed_at",
            "cancel_reason",
        )
    }


async def schedule_training(
    session: AsyncSession,
    actor: Actor,
    cid: int,
    *,
    starts_at: datetime,
    place: str,
    contact_user_id: int | None,
    comment: str | None,
) -> TrainingSession:
    actor.require(Perm.TRAINING_MANAGE)
    c = await session.get(Candidate, cid, with_for_update=True)
    if c is None:
        raise NotFound("Анкета не найдена")
    if c.status != CandidateStatus.APPROVED:
        raise Conflict("bad_state", "Обучение назначается одобренному кандидату")
    if not place.strip():
        raise AppError("place_required", "Укажите место")
    contact = await session.get(User, contact_user_id) if contact_user_id else None
    t = TrainingSession(
        candidate_id=c.id,
        starts_at=starts_at,
        place=place.strip(),
        contact_user_id=contact.id if contact else None,
        comment=(comment or "").strip() or None,
        created_by_id=actor.id,
    )
    session.add(t)
    await session.flush()
    ev = await _event(session, c, "training_scheduled", actor.id, training_id=t.id)
    text = (
        f"Вам назначено обучение: {timeutil.format_msk(starts_at)}, {escape(t.place)}"
        + (
            f"\nКонтакт: {escape(contact.full_name)}"
            + (f" (@{escape(contact.username)})" if contact.username else "")
            if contact
            else ""
        )
        + (f"\n{escape(t.comment)}" if t.comment else "")
        + "\nПодтвердите участие в приложении."
    )
    await _tell_candidate(session, c, f"candidate_event:{ev.id}", text, "Подтвердить")
    await session.commit()
    return t


async def confirm_training(session: AsyncSession, user: User, training_id: int) -> TrainingSession:
    t = await session.get(TrainingSession, training_id, with_for_update=True)
    c = await session.get(Candidate, t.candidate_id) if t else None
    if t is None or c.user_id != user.id:
        raise NotFound("Обучение не найдено")
    if t.status == "cancelled":
        raise Conflict("cancelled", "Обучение отменено")
    if t.status != "confirmed":
        t.status = "confirmed"
        t.confirmed_at = timeutil.now_utc()
        await _event(session, c, "training_confirmed", user.id, training_id=t.id)
        await session.commit()
    return t


async def cancel_training(
    session: AsyncSession, actor: Actor, training_id: int, reason: str | None, force: bool
) -> TrainingSession:
    actor.require(Perm.TRAINING_MANAGE)
    t = await session.get(TrainingSession, training_id, with_for_update=True)
    if t is None:
        raise NotFound("Обучение не найдено")
    reason = (reason or "").strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину отмены")
    if t.status == "cancelled":
        return t
    if t.status == "confirmed" and not force:
        raise Conflict(
            "confirm_required", "Кандидат уже подтвердил обучение — подтвердите отмену ещё раз"
        )
    t.status = "cancelled"
    t.cancel_reason = reason
    c = await session.get(Candidate, t.candidate_id)
    ev = await _event(session, c, "training_cancelled", actor.id, training_id=t.id, reason=reason)
    await _tell_candidate(
        session,
        c,
        f"candidate_event:{ev.id}",
        f"Обучение {timeutil.format_msk(t.starts_at)} отменено.\nПричина: {escape(reason)}",
    )
    await session.commit()
    return t


async def start_training(session: AsyncSession, actor: Actor, cid: int) -> User:
    """«Начать обучение»: создаётся или активируется профиль.

    Роль и продуктовые доступы владелец назначает отдельно.
    """
    actor.require(Perm.TRAINING_MANAGE)
    c = await session.get(Candidate, cid, with_for_update=True)
    if c is None:
        raise NotFound("Анкета не найдена")
    if c.status == CandidateStatus.TRAINING:
        return await session.get(User, c.user_id)
    if c.status != CandidateStatus.APPROVED:
        raise Conflict("bad_state", "Начать обучение можно для одобренного кандидата")
    user = await session.get(User, c.user_id, with_for_update=True)
    old = {"status": user.status, "training_status": user.training_status}
    if user.status in (AccountStatus.UNREGISTERED, AccountStatus.REJECTED, AccountStatus.PENDING):
        # ФИО из анкеты — только для ещё не зарегистрированного человека: подтверждённое
        # ФИО меняется только через заявку на смену.
        user.first_name, user.last_name = c.first_name, c.last_name
        user.status = AccountStatus.ACTIVE
        # Открытая заявка на регистрацию больше не нужна: её отклонение потом не должно
        # отключить действующего стажёра.
        await session.execute(
            update(RegistrationRequest)
            .where(
                RegistrationRequest.user_id == user.id,
                RegistrationRequest.status == RequestStatus.PENDING,
            )
            .values(
                status=RequestStatus.CANCELLED,
                reason="Начато обучение по анкете кандидата",
                decided_at=timeutil.now_utc(),
                decided_by_id=actor.id,
            )
        )
    user.training_status = "training"
    user.training_started_at = timeutil.now_utc()
    if user.referred_by_id is None:
        user.referred_by_id = c.referrer_id
    c.status = CandidateStatus.TRAINING
    ev = await _event(session, c, "training_started", actor.id)
    await _ref_event(session, c.referrer_id, "training", candidate_id=c.id)
    audit.record(
        session,
        category=Category.USERS,
        action="training.started",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="candidate",
        object_id=c.id,
        old=old,
        new={"status": user.status, "training_status": "training"},
    )
    await _tell_candidate(
        session,
        c,
        f"candidate_event:{ev.id}",
        "Обучение началось. Доступы к продуктам выдаст администрация.",
        "Открыть приложение",
    )
    await notify.notify_holders(
        session,
        None,
        dedup_key=f"candidate_event:{ev.id}:owner",
        text=f"{escape(user.full_name)} начал(а) обучение — назначьте роль и доступы к продуктам.",
        button="Открыть",
    )
    await session.commit()
    return user


async def _training_products(session: AsyncSession, user_id: int) -> list[Product]:
    rows = await session.execute(
        select(Product)
        .join(ProductAccess, ProductAccess.product_id == Product.id)
        .where(
            ProductAccess.user_id == user_id,
            Product.training_volume > 0,
            Product.status != ProductStatus.DELETED,
        )
        .order_by(Product.name)
    )
    return list(rows.scalars())


async def training_progress(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    """Фактические, подтверждённые администратором и учитываемые в оплате учебные единицы."""
    user = await session.get(User, user_id)
    since = user.training_started_at
    out = []
    for p in await _training_products(session, user_id):
        base = select(func.coalesce(func.sum(Task.units), 0)).where(
            Task.assignee_id == user_id, Task.product_id == p.id
        )
        if since:
            base = base.where(Task.completed_at >= since)
        actual = int(await session.scalar(base.where(Task.status.in_(DONE_STATUSES))))
        paid = int(
            await session.scalar(
                base.where(Task.status.in_([TaskStatus.APPROVED, TaskStatus.PAID]))
            )
        )
        last = await session.scalar(
            select(TrainingConfirmation)
            .where(TrainingConfirmation.user_id == user_id, TrainingConfirmation.product_id == p.id)
            .order_by(TrainingConfirmation.id.desc())
            .limit(1)
        )
        confirmed = last.confirmed_units if last else 0
        if confirmed >= p.training_volume:
            state = "done"
        elif actual >= p.training_volume:
            state = "awaiting_confirmation"  # ожидает подтверждения администратора
        else:
            state = "in_progress"
        out.append(
            {
                "product_id": p.id,
                "product": p.name,
                "volume": p.training_volume,
                "actual_units": actual,
                "confirmed_units": confirmed,
                "paid_units": paid,
                "state": state,
            }
        )
    return out


async def confirm_units(
    session: AsyncSession,
    actor: Actor,
    user_id: int,
    product_id: int,
    confirmed: int,
    comment: str | None,
) -> dict[str, Any]:
    """Администратор подтверждает весь объём или его часть; изменения сохраняются историей."""
    actor.require(Perm.TRAINING_MANAGE)
    if user_id == actor.id and not actor.is_owner:
        raise Forbidden("self_action", "Это действие нельзя выполнить над собой")
    user = await session.get(User, user_id, with_for_update=True)
    if user is None or user.training_status == "none":
        raise NotFound("Стажёр не найден")
    progress = {p["product_id"]: p for p in await training_progress(session, user_id)}
    if product_id not in progress:
        raise NotFound("У стажёра нет этого учебного продукта")
    if confirmed < 0 or confirmed > progress[product_id]["actual_units"]:
        raise AppError("bad_units", "Подтвердить можно от 0 до фактически выполненного объёма")
    session.add(
        TrainingConfirmation(
            user_id=user_id,
            product_id=product_id,
            confirmed_units=confirmed,
            actual_units=progress[product_id]["actual_units"],
            author_id=actor.id,
            comment=(comment or "").strip() or None,
        )
    )
    audit.record(
        session,
        category=Category.USERS,
        action="training.units_confirmed",
        actor_id=actor.id,
        target_user_id=user_id,
        object_type="product",
        object_id=product_id,
        old={"confirmed_units": progress[product_id]["confirmed_units"]},
        new={"confirmed_units": confirmed, "actual_units": progress[product_id]["actual_units"]},
        reason=comment,
    )
    await session.flush()
    progress = await training_progress(session, user_id)
    all_done = bool(progress) and all(p["state"] == "done" for p in progress)
    await _sync_training_status(session, actor, user, all_done)
    await session.commit()
    return {"progress": progress, "training_status": user.training_status}


async def _sync_training_status(
    session: AsyncSession, actor: Actor, user: User, done: bool
) -> None:
    """Завершение обучения начисляет реферальный бонус; откат — корректировкой, не удалением."""
    if done and user.training_status != "completed":
        user.training_status = "completed"
        c = await session.scalar(
            select(Candidate)
            .where(Candidate.user_id == user.id)
            .order_by(Candidate.id.desc())
            .limit(1)
        )
        await _ref_event(
            session, user.referred_by_id, "training_done", candidate_id=c.id if c else None
        )
        amount = await settings_svc.get(session, "referral_bonus_kop")
        if user.referred_by_id and amount and not await _active_referral_bonus(session, user.id):
            n = await session.scalar(
                select(func.count()).where(LedgerEntry.dedup_key.like(f"referral:{user.id}:%"))
            )
            entry = LedgerEntry(
                user_id=user.referred_by_id,
                kind=EntryKind.REFERRAL_BONUS,
                amount_kop=amount,
                occurred_at=timeutil.now_utc(),
                author_id=actor.id,
                reason=f"Реферальный бонус: {user.full_name} прошёл(а) обучение",
                dedup_key=f"referral:{user.id}:{n + 1}",
            )
            session.add(entry)
            await session.flush()
            await _ref_event(
                session, user.referred_by_id, "bonus", candidate_id=c.id if c else None
            )
            ref = await session.get(User, user.referred_by_id)
            await outbox.enqueue(
                session,
                dedup_key=f"ledger:{entry.id}",
                chat_id=ref.telegram_id,
                related_user_id=ref.id,
                text=(
                    f"Реферальный бонус {rub(amount)}: {escape(user.full_name)} прошёл(а) обучение"
                ),
            )
    elif not done and user.training_status == "completed":
        user.training_status = "training"
        bonus = await _active_referral_bonus(session, user.id)
        if bonus is not None:
            session.add(
                LedgerEntry(
                    user_id=bonus.user_id,
                    kind=EntryKind.BONUS_REVERSAL,
                    amount_kop=-bonus.amount_kop,
                    occurred_at=timeutil.now_utc(),
                    author_id=actor.id,
                    reason=f"Подтверждённый объём обучения {user.full_name} уменьшен ниже порога",
                    source_entry_id=bonus.id,
                    dedup_key=f"bonus_reversal:{bonus.id}",
                )
            )


async def _active_referral_bonus(session: AsyncSession, trainee_id: int) -> LedgerEntry | None:
    reversed_ids = select(LedgerEntry.source_entry_id).where(
        LedgerEntry.kind == EntryKind.BONUS_REVERSAL, LedgerEntry.source_entry_id.is_not(None)
    )
    return await session.scalar(
        select(LedgerEntry)
        .where(
            LedgerEntry.kind == EntryKind.REFERRAL_BONUS,
            LedgerEntry.dedup_key.like(f"referral:{trainee_id}:%"),
            LedgerEntry.id.not_in(reversed_ids),
        )
        .order_by(LedgerEntry.id.desc())
        .limit(1)
    )


async def trainees(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    actor.require(Perm.TRAINING_MANAGE)
    users = (
        (
            await session.execute(
                select(User)
                .where(User.training_status.in_(["training", "completed"]))
                .order_by(User.last_name)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "user_id": u.id,
            "full_name": u.full_name,
            "training_status": u.training_status,
            "training_started_at": u.training_started_at,
            "progress": await training_progress(session, u.id),
        }
        for u in users
    ]
