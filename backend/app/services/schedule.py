"""Рабочий график, точки, сводка по дням и распределение (ТЗ 9)."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import (
    ACTIVE_SHIFT,
    DONE_STATUSES,
    AccountStatus,
    DayOff,
    Product,
    Shift,
    ShiftPlan,
    ShiftStatus,
    Task,
    User,
    UserNetwork,
    WorkPoint,
)
from app.permissions import Perm
from app.services import audit, notify, outbox
from app.services import products as products_svc
from app.services import settings as settings_svc
from app.services.access import ALL, Actor, Scope, get_target, scope_for, team_of
from app.services.audit import Category

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
PLAN_DEADLINE = time(22, 0)  # плановые объёмы — до 22:00 предыдущего дня


# --- точки --------------------------------------------------------------------------


async def create_point(
    session: AsyncSession, actor: Actor, name: str, address: str | None, capacity: int | None
) -> WorkPoint:
    actor.require_owner()
    name = " ".join(name.split())
    if not name:
        raise AppError("invalid_name", "Укажите название точки")
    if await session.scalar(select(WorkPoint.id).where(func.lower(WorkPoint.name) == name.lower())):
        raise Conflict("duplicate", "Точка с таким названием уже есть")
    if capacity is not None and capacity < 1:
        raise AppError("bad_capacity", "Вместимость — не меньше 1")
    p = WorkPoint(name=name, address=(address or "").strip() or None, capacity=capacity)
    session.add(p)
    await session.flush()
    audit.record(
        session,
        category=Category.SCHEDULE,
        action="point.created",
        actor_id=actor.id,
        object_type="work_point",
        object_id=p.id,
        new={"name": p.name, "address": p.address, "capacity": capacity},
    )
    await session.commit()
    return p


async def update_point(
    session: AsyncSession, actor: Actor, point_id: int, changes: dict
) -> WorkPoint:
    actor.require_owner()
    p = await session.get(WorkPoint, point_id, with_for_update=True)
    if p is None:
        raise NotFound("Точка не найдена")
    old = {
        "name": p.name,
        "address": p.address,
        "capacity": p.capacity,
        "is_archived": p.is_archived,
    }
    if "name" in changes:
        name = " ".join((changes["name"] or "").split())
        if not name:
            raise AppError("invalid_name", "Укажите название точки")
        clash = await session.scalar(
            select(WorkPoint.id).where(
                func.lower(WorkPoint.name) == name.lower(), WorkPoint.id != p.id
            )
        )
        if clash:
            raise Conflict("duplicate", "Точка с таким названием уже есть")
        p.name = name
    if "address" in changes:
        p.address = (changes["address"] or "").strip() or None
    if "capacity" in changes:
        if changes["capacity"] is not None and changes["capacity"] < 1:
            raise AppError("bad_capacity", "Вместимость — не меньше 1")
        p.capacity = changes["capacity"]
    if "is_archived" in changes:
        p.is_archived = bool(changes["is_archived"])
    new = {
        "name": p.name,
        "address": p.address,
        "capacity": p.capacity,
        "is_archived": p.is_archived,
    }
    if new != old:
        audit.record(
            session,
            category=Category.SCHEDULE,
            action="point.updated",
            actor_id=actor.id,
            object_type="work_point",
            object_id=p.id,
            old=old,
            new=new,
        )
    await session.commit()
    return p


# --- проверки -----------------------------------------------------------------------


def booking_window(today: date) -> tuple[date, date]:
    """Сотрудник встаёт в график на текущую и следующую неделю."""
    return today, timeutil.week_start(today) + timedelta(days=13)


def time_point_deadline(day: date) -> datetime:
    return timeutil.msk_at(day)


def plan_deadline(day: date) -> datetime:
    return timeutil.msk_at(day - timedelta(days=1), PLAN_DEADLINE)


async def _check_capacity(
    session: AsyncSession, point: WorkPoint, day: date, start: time, end: time, exclude: int | None
) -> None:
    """Подтверждённые смены, пересекающиеся по времени, не должны превышать вместимость.

    Точка блокируется, поэтому два параллельных запроса не займут последнее место вдвоём.
    """
    await session.refresh(point, with_for_update=True)
    if point.capacity is None:
        return
    # Максимальное число одновременных смен — по точкам начала внутри нового интервала.
    rows = (
        await session.execute(
            select(Shift.start_time, Shift.end_time).where(
                Shift.point_id == point.id,
                Shift.day == day,
                Shift.status == ShiftStatus.CONFIRMED,
                Shift.start_time < end,
                Shift.end_time > start,
                *([Shift.id != exclude] if exclude else []),
            )
        )
    ).all()
    moments = [start] + [s for s, _ in rows if start <= s < end]
    peak = max(sum(1 for s, e in rows if s <= m < e) for m in moments) if rows else 0
    if peak + 1 > point.capacity:
        raise Conflict(
            "point_full",
            f"На точке «{point.name}» в это время нет мест (вместимость {point.capacity})",
        )


async def _validate_plans(
    session: AsyncSession, user: User, plans: dict[int, int]
) -> dict[int, int]:
    plans = {pid: u for pid, u in plans.items() if u}
    if any(u < 0 or u > 10000 for u in plans.values()):
        raise AppError("bad_plan", "План — от 0 до 10 000 единиц")
    allowed = await products_svc.user_product_ids(session, user.id)
    if bad := set(plans) - allowed:
        raise Forbidden("no_product_access", f"Нет доступа к продуктам: {sorted(bad)}")
    return plans


def _check_times(start: time, end: time) -> None:
    if end <= start:
        raise AppError("bad_time", "Время окончания должно быть позже начала")


async def _point(session: AsyncSession, point_id: int) -> WorkPoint:
    p = await session.get(WorkPoint, point_id)
    if p is None or p.is_archived:
        raise NotFound("Точка не найдена")
    return p


# --- смены --------------------------------------------------------------------------


async def create_shift(
    session: AsyncSession,
    actor: Actor,
    *,
    user_id: int,
    day: date,
    start: time,
    end: time,
    point_id: int,
    plans: dict[int, int],
) -> Shift:
    by_manager = user_id != actor.id
    if by_manager:
        user = await get_target(session, actor, Perm.SCHEDULE_MANAGE, user_id)
    else:
        user = actor.user
    user = await session.get(User, user.id, with_for_update=True)
    if user.status != AccountStatus.ACTIVE:
        raise Conflict("not_active", "Аккаунт не активен")
    _check_times(start, end)
    now = timeutil.now_utc()
    today = timeutil.msk_date(now)
    if not by_manager:
        lo, hi = booking_window(today)
        if not lo <= day <= hi:
            raise Conflict("out_of_window", "Встать в график можно на текущую и следующую неделю")
    elif day < today:
        raise Conflict("past_day", "Нельзя создать смену в прошлом")
    if await session.scalar(
        select(Shift.id).where(
            Shift.user_id == user.id, Shift.day == day, Shift.status.in_(ACTIVE_SHIFT)
        )
    ):
        raise Conflict("already_scheduled", "На этот день смена уже есть")
    plans = await _validate_plans(session, user, plans)
    point = await _point(session, point_id)

    # Смена на сегодня, поставленная самим сотрудником, требует подтверждения (ТЗ 9).
    status = ShiftStatus.PENDING if (day == today and not by_manager) else ShiftStatus.CONFIRMED
    if status == ShiftStatus.CONFIRMED:
        await _check_capacity(session, point, day, start, end, None)

    await session.execute(delete(DayOff).where(DayOff.user_id == user.id, DayOff.day == day))
    shift = Shift(
        user_id=user.id,
        day=day,
        start_time=start,
        end_time=end,
        point_id=point.id,
        status=status,
        created_by_id=actor.id,
        decided_by_id=actor.id if status == ShiftStatus.CONFIRMED else None,
        decided_at=now if status == ShiftStatus.CONFIRMED else None,
    )
    session.add(shift)
    await session.flush()
    for pid, units in plans.items():
        session.add(ShiftPlan(shift_id=shift.id, product_id=pid, units=units))
    ev = audit.record(
        session,
        category=Category.SCHEDULE,
        action="shift.created",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="shift",
        object_id=shift.id,
        new=_shift_dict(shift, plans, point),
    )
    await session.flush()
    if status == ShiftStatus.PENDING:
        await notify.notify_holders(
            session,
            Perm.SCHEDULE_MANAGE,
            dedup_key=f"audit:{ev.id}",
            text=(
                f"<b>Смена на сегодня ждёт подтверждения</b>\n{escape(user.full_name)}, "
                f"{start:%H:%M}–{end:%H:%M}, {escape(point.name)}"
            ),
            button="Открыть",
        )
    await session.commit()
    return shift


def _shift_dict(shift: Shift, plans: dict[int, int], point: WorkPoint) -> dict[str, Any]:
    return {
        "day": shift.day.isoformat(),
        "start": shift.start_time.strftime("%H:%M"),
        "end": shift.end_time.strftime("%H:%M"),
        "point": point.name,
        "status": shift.status.value,
        "plans": {str(k): v for k, v in plans.items()},
    }


async def _shift_for_edit(session: AsyncSession, actor: Actor, shift_id: int) -> tuple[Shift, bool]:
    shift = await session.get(Shift, shift_id, with_for_update=True)
    if shift is None:
        raise NotFound("Смена не найдена")
    if shift.user_id == actor.id:
        return shift, False
    await get_target(session, actor, Perm.SCHEDULE_MANAGE, shift.user_id)
    return shift, True


async def _plans_of(session: AsyncSession, shift_id: int) -> dict[int, int]:
    rows = await session.execute(
        select(ShiftPlan.product_id, ShiftPlan.units).where(ShiftPlan.shift_id == shift_id)
    )
    return dict(rows.all())


async def update_shift(
    session: AsyncSession, actor: Actor, shift_id: int, *, start: time, end: time, point_id: int
) -> Shift:
    shift, by_manager = await _shift_for_edit(session, actor, shift_id)
    if shift.status not in ACTIVE_SHIFT:
        raise Conflict("bad_state", "Смена отменена")
    now = timeutil.now_utc()
    if not by_manager and now >= time_point_deadline(shift.day):
        raise Conflict("too_late", "Время и точку можно менять до 00:00 дня смены")
    _check_times(start, end)
    point = await _point(session, point_id)
    old_point = await session.get(WorkPoint, shift.point_id)
    plans = await _plans_of(session, shift.id)
    old = _shift_dict(shift, plans, old_point)
    if shift.status == ShiftStatus.CONFIRMED:
        await _check_capacity(session, point, shift.day, start, end, shift.id)
    shift.start_time, shift.end_time, shift.point_id = start, end, point.id
    audit.record(
        session,
        category=Category.SCHEDULE,
        action="shift.updated",
        actor_id=actor.id,
        target_user_id=shift.user_id,
        object_type="shift",
        object_id=shift.id,
        old=old,
        new=_shift_dict(shift, plans, point),
    )
    await session.commit()
    return shift


async def set_plans(
    session: AsyncSession, actor: Actor, shift_id: int, plans: dict[int, int]
) -> Shift:
    shift, by_manager = await _shift_for_edit(session, actor, shift_id)
    if shift.status not in ACTIVE_SHIFT:
        raise Conflict("bad_state", "Смена отменена")
    if not by_manager and timeutil.now_utc() >= plan_deadline(shift.day):
        raise Conflict("too_late", "Плановые объёмы можно менять до 22:00 предыдущего дня")
    user = await session.get(User, shift.user_id)
    plans = await _validate_plans(session, user, plans)
    old = await _plans_of(session, shift.id)
    if old == plans:
        return shift
    await session.execute(delete(ShiftPlan).where(ShiftPlan.shift_id == shift.id))
    for pid, units in plans.items():
        session.add(ShiftPlan(shift_id=shift.id, product_id=pid, units=units))
    audit.record(
        session,
        category=Category.SCHEDULE,
        action="shift.plans_changed",
        actor_id=actor.id,
        target_user_id=shift.user_id,
        object_type="shift",
        object_id=shift.id,
        old={str(k): v for k, v in old.items()},
        new={str(k): v for k, v in plans.items()},
    )
    await session.commit()
    return shift


async def cancel_shift(
    session: AsyncSession, actor: Actor, shift_id: int, reason: str | None
) -> Shift:
    shift, by_manager = await _shift_for_edit(session, actor, shift_id)
    if shift.status not in ACTIVE_SHIFT:
        return shift
    if not by_manager and timeutil.now_utc() >= time_point_deadline(shift.day):
        raise Conflict("too_late", "Отменить смену самостоятельно можно до 00:00 дня смены")
    reason = (reason or "").strip() or None
    if by_manager and not reason:
        raise AppError("reason_required", "Укажите причину отмены")
    old = shift.status
    shift.status = ShiftStatus.CANCELLED
    shift.reason = reason
    shift.decided_by_id = actor.id
    shift.decided_at = timeutil.now_utc()
    ev = audit.record(
        session,
        category=Category.SCHEDULE,
        action="shift.cancelled",
        actor_id=actor.id,
        target_user_id=shift.user_id,
        object_type="shift",
        object_id=shift.id,
        old={"status": old},
        new={"status": ShiftStatus.CANCELLED},
        reason=reason,
    )
    await session.flush()
    if by_manager:
        user = await session.get(User, shift.user_id)
        await outbox.enqueue(
            session,
            dedup_key=f"audit:{ev.id}",
            chat_id=user.telegram_id,
            related_user_id=user.id,
            text=f"Смена {shift.day:%d.%m} отменена.\nПричина: {escape(reason)}",
        )
    await session.commit()
    return shift


async def decide_shift(
    session: AsyncSession, actor: Actor, shift_id: int, approve: bool, reason: str | None
) -> Shift:
    shift = await session.get(Shift, shift_id, with_for_update=True)
    if shift is None:
        raise NotFound("Смена не найдена")
    await get_target(session, actor, Perm.SCHEDULE_MANAGE, shift.user_id)
    if shift.status != ShiftStatus.PENDING:
        raise Conflict("already_decided", "Заявка на смену уже рассмотрена")
    reason = (reason or "").strip() or None
    point = await session.get(WorkPoint, shift.point_id)
    if approve:
        await _check_capacity(session, point, shift.day, shift.start_time, shift.end_time, shift.id)
        shift.status = ShiftStatus.CONFIRMED
        text = (
            f"Смена {shift.day:%d.%m} {shift.start_time:%H:%M}–{shift.end_time:%H:%M} подтверждена"
        )
    else:
        if not reason:
            raise AppError("reason_required", "Укажите причину")
        shift.status = ShiftStatus.REJECTED
        text = f"Заявка на смену {shift.day:%d.%m} отклонена.\nПричина: {escape(reason)}"
    shift.reason = reason
    shift.decided_by_id = actor.id
    shift.decided_at = timeutil.now_utc()
    ev = audit.record(
        session,
        category=Category.SCHEDULE,
        action="shift.confirmed" if approve else "shift.rejected",
        actor_id=actor.id,
        target_user_id=shift.user_id,
        object_type="shift",
        object_id=shift.id,
        old={"status": ShiftStatus.PENDING},
        new={"status": shift.status},
        reason=reason,
    )
    await session.flush()
    user = await session.get(User, shift.user_id)
    await outbox.enqueue(
        session,
        dedup_key=f"audit:{ev.id}",
        chat_id=user.telegram_id,
        related_user_id=user.id,
        text=text,
    )
    await session.commit()
    return shift


async def set_day_off(
    session: AsyncSession, actor: Actor, user_id: int, day: date, on: bool
) -> None:
    if user_id != actor.id:
        await get_target(session, actor, Perm.SCHEDULE_MANAGE, user_id)
    elif day < timeutil.msk_today():
        raise Conflict("past_day", "Нельзя изменить прошедший день")
    if on:
        if await session.scalar(
            select(Shift.id).where(
                Shift.user_id == user_id, Shift.day == day, Shift.status.in_(ACTIVE_SHIFT)
            )
        ):
            raise Conflict("has_shift", "На этот день есть смена — сначала отмените её")
        exists = await session.scalar(
            select(DayOff.id).where(DayOff.user_id == user_id, DayOff.day == day)
        )
        if not exists:
            session.add(DayOff(user_id=user_id, day=day, created_by_id=actor.id))
    else:
        await session.execute(delete(DayOff).where(DayOff.user_id == user_id, DayOff.day == day))
    audit.record(
        session,
        category=Category.SCHEDULE,
        action="day_off.set" if on else "day_off.cleared",
        actor_id=actor.id,
        target_user_id=user_id,
        object_type="day",
        object_id=day.isoformat(),
    )
    await session.commit()


# --- чтение -------------------------------------------------------------------------


@dataclass
class ShiftView:
    shift: Shift
    point: WorkPoint
    plans: dict[int, int]


async def shifts_between(
    session: AsyncSession, user_ids: Scope, start: date, end: date, statuses=ACTIVE_SHIFT
) -> list[ShiftView]:
    q = (
        select(Shift, WorkPoint)
        .join(WorkPoint, WorkPoint.id == Shift.point_id)
        .where(Shift.day >= start, Shift.day <= end, Shift.status.in_(statuses))
        .order_by(Shift.day, Shift.start_time)
    )
    if user_ids is not ALL:
        q = q.where(Shift.user_id.in_(user_ids))
    rows = (await session.execute(q)).all()
    plans: dict[int, dict[int, int]] = defaultdict(dict)
    if rows:
        for sid, pid, units in await session.execute(
            select(ShiftPlan.shift_id, ShiftPlan.product_id, ShiftPlan.units).where(
                ShiftPlan.shift_id.in_([s.id for s, _ in rows])
            )
        ):
            plans[sid][pid] = units
    return [ShiftView(s, p, plans.get(s.id, {})) for s, p in rows]


async def actual_units(
    session: AsyncSession,
    user_ids: Scope,
    start: date,
    end: date,
    product_ids: list[int] | None = None,
) -> dict[tuple[int, date, int], int]:
    """Фактически выполненные единицы по дню завершения задания (МСК)."""
    lo, hi = timeutil.msk_day_bounds(start, end)
    day = func.date(func.timezone("Europe/Moscow", Task.completed_at))
    q = (
        select(Task.assignee_id, day, Task.product_id, func.sum(Task.units))
        .where(Task.completed_at >= lo, Task.completed_at < hi, Task.status.in_(DONE_STATUSES))
        .group_by(Task.assignee_id, day, Task.product_id)
    )
    if user_ids is not ALL:
        q = q.where(Task.assignee_id.in_(user_ids))
    if product_ids:
        q = q.where(Task.product_id.in_(product_ids))
    return {(u, d, p): int(n) for u, d, p, n in await session.execute(q)}


async def days_off(
    session: AsyncSession, user_ids: Scope, start: date, end: date
) -> set[tuple[int, date]]:
    q = select(DayOff.user_id, DayOff.day).where(DayOff.day >= start, DayOff.day <= end)
    if user_ids is not ALL:
        q = q.where(DayOff.user_id.in_(user_ids))
    return {(u, d) for u, d in await session.execute(q)}


def day_status(day: date, today: date, shift: Shift | None, day_off: bool, actual: int) -> str:
    """Статусы дня (ТЗ 9). Плановая смена не доказывает фактическую работу."""
    if day < today:
        if actual > 0:
            return "worked"
        if day_off:
            return "day_off"
        if shift is not None and shift.status == ShiftStatus.CONFIRMED:
            return "no_show"
        return "none"
    if shift is not None:
        if shift.status == ShiftStatus.PENDING:
            return "pending"
        return "working_today" if day == today else "confirmed"
    if day_off:
        return "day_off"
    if day == today and actual > 0:
        return "worked"
    return "none"


async def forecast(
    session: AsyncSession, user: User, plans: dict[int, int]
) -> tuple[int | None, list[str]]:
    total, problems = 0, []
    for pid, units in plans.items():
        try:
            total += units * await products_svc.resolve_rate(session, user, pid)
        except AppError as e:
            problems.append(e.message)
    return (None if problems else total), problems


async def my_schedule(
    session: AsyncSession, actor: Actor, start: date | None = None
) -> dict[str, Any]:
    user = actor.user
    today = timeutil.msk_today()
    start = start or timeutil.week_start(today)
    end = start + timedelta(days=13)
    views = {
        v.shift.day: v for v in await shifts_between(session, frozenset({user.id}), start, end)
    }
    offs = await days_off(session, frozenset({user.id}), start, end)
    fact = await actual_units(session, frozenset({user.id}), start, end)
    names = dict((await session.execute(select(Product.id, Product.name))).all())
    now = timeutil.now_utc()
    days = []
    for i in range(14):
        d = start + timedelta(days=i)
        v = views.get(d)
        actual = sum(n for (u, dd, _), n in fact.items() if dd == d)
        item: dict[str, Any] = {
            "day": d,
            "weekday": WEEKDAYS[d.weekday()],
            "status": day_status(d, today, v.shift if v else None, (user.id, d) in offs, actual),
            "actual_units": actual,
            "shift": None,
        }
        if v:
            money, problems = await forecast(session, user, v.plans)
            item["shift"] = {
                "id": v.shift.id,
                "start": v.shift.start_time.strftime("%H:%M"),
                "end": v.shift.end_time.strftime("%H:%M"),
                "point_id": v.point.id,
                "point": v.point.name,
                "status": v.shift.status.value,
                "plans": [
                    {"product_id": pid, "product": names.get(pid), "units": u}
                    for pid, u in v.plans.items()
                ],
                "forecast_kop": money,
                "forecast_problems": problems,
                "can_edit_time": now < time_point_deadline(d),
                "can_edit_plans": now < plan_deadline(d),
            }
        days.append(item)
    lo, hi = booking_window(today)
    min_shifts = await settings_svc.get(session, "min_shifts_per_week")
    weeks = []
    for w in range(2):
        ws = start + timedelta(days=7 * w)
        n = sum(
            1
            for i in range(7)
            if (v := views.get(ws + timedelta(days=i))) and v.shift.status == ShiftStatus.CONFIRMED
        )
        weeks.append({"start": ws, "confirmed_shifts": n, "min_shifts": min_shifts})
    return {"days": days, "weeks": weeks, "book_from": lo, "book_to": hi, "today": today}


async def _filter_people(
    session: AsyncSession,
    actor: Actor,
    perm: Perm,
    *,
    network_id: int | None,
    coach_id: int | None,
    instructor_id: int | None,
    user_id: int | None,
) -> list[User]:
    actor.require(perm)
    scope = await scope_for(session, actor, perm)
    q = select(User).where(User.status == AccountStatus.ACTIVE)
    if scope is not ALL:
        q = q.where(User.id.in_(scope))
    if network_id:
        q = q.where(
            User.id.in_(select(UserNetwork.user_id).where(UserNetwork.network_id == network_id))
        )
    for lead in (coach_id, instructor_id):
        if lead:
            q = q.where(User.id.in_(await team_of(session, lead) or {0}))
    if user_id:
        q = q.where(User.id == user_id)
    return list((await session.execute(q.order_by(User.last_name, User.first_name))).scalars())


async def summary(
    session: AsyncSession,
    actor: Actor,
    *,
    start: date,
    end: date,
    network_id: int | None = None,
    coach_id: int | None = None,
    instructor_id: int | None = None,
    user_id: int | None = None,
    product_id: int | None = None,
) -> dict[str, Any]:
    """Сводка по дням (ТЗ 9.1): кто работает, план, факт, выполнение плана."""
    if end < start or (end - start).days > 62:
        raise AppError("bad_period", "Период — не больше двух месяцев")
    if not (actor.has(Perm.SCHEDULE_VIEW) or actor.has(Perm.SCHEDULE_MANAGE)):
        raise Forbidden("no_permission", "Недостаточно прав для этого действия")
    perm = Perm.SCHEDULE_MANAGE if actor.has(Perm.SCHEDULE_MANAGE) else Perm.SCHEDULE_VIEW
    people = await _filter_people(
        session,
        actor,
        perm,
        network_id=network_id,
        coach_id=coach_id,
        instructor_id=instructor_id,
        user_id=user_id,
    )
    ids = frozenset(u.id for u in people)
    views = await shifts_between(session, ids, start, end)
    by_key = {(v.shift.user_id, v.shift.day): v for v in views}
    offs = await days_off(session, ids, start, end)
    fact = await actual_units(session, ids, start, end, [product_id] if product_id else None)
    today = timeutil.msk_today()

    rows, totals = (
        [],
        defaultdict(
            lambda: {"planned": 0, "actual": 0, "people": 0, "pending": 0, "day_off": 0, "none": 0}
        ),
    )
    d = start
    while d <= end:
        for u in people:
            v = by_key.get((u.id, d))
            planned = 0
            if v and v.shift.status == ShiftStatus.CONFIRMED:
                planned = v.plans.get(product_id, 0) if product_id else sum(v.plans.values())
            actual = sum(n for (uid, dd, _), n in fact.items() if uid == u.id and dd == d)
            status = day_status(d, today, v.shift if v else None, (u.id, d) in offs, actual)
            t = totals[d]
            t["planned"] += planned
            t["actual"] += actual
            if status in ("confirmed", "working_today", "worked"):
                t["people"] += 1
            if status == "pending":
                t["pending"] += 1
            if status == "day_off":
                t["day_off"] += 1
            if status == "none":
                t["none"] += 1
            rows.append(
                {
                    "day": d,
                    "user_id": u.id,
                    "full_name": u.full_name,
                    "status": status,
                    "start": v.shift.start_time.strftime("%H:%M") if v else None,
                    "end": v.shift.end_time.strftime("%H:%M") if v else None,
                    "point": v.point.name if v else None,
                    "planned_units": planned,
                    "actual_units": actual,
                    "plan_percent": round(actual * 100 / planned, 1) if planned else None,
                }
            )
        d += timedelta(days=1)
    total_planned = sum(t["planned"] for t in totals.values())
    total_actual = sum(t["actual"] for t in totals.values())
    return {
        "rows": rows,
        "days": [{"day": k, **v} for k, v in sorted(totals.items())],
        "total": {
            "planned_units": total_planned,
            "actual_units": total_actual,
            "plan_percent": round(total_actual * 100 / total_planned, 1) if total_planned else None,
        },
    }


async def distribution(
    session: AsyncSession,
    actor: Actor,
    *,
    day: date,
    network_id: int | None = None,
    coach_id: int | None = None,
    instructor_id: int | None = None,
    product_id: int | None = None,
) -> dict[str, Any]:
    """«Распред» (ТЗ 9.2): подтверждённые работники дня по точкам, план и факт."""
    if not (actor.has(Perm.SCHEDULE_VIEW) or actor.has(Perm.SCHEDULE_MANAGE)):
        raise Forbidden("no_permission", "Недостаточно прав для этого действия")
    perm = Perm.SCHEDULE_MANAGE if actor.has(Perm.SCHEDULE_MANAGE) else Perm.SCHEDULE_VIEW
    people = {
        u.id: u
        for u in await _filter_people(
            session,
            actor,
            perm,
            network_id=network_id,
            coach_id=coach_id,
            instructor_id=instructor_id,
            user_id=None,
        )
    }
    views = [
        v
        for v in await shifts_between(
            session, frozenset(people), day, day, (ShiftStatus.CONFIRMED,)
        )
        if not product_id or product_id in v.plans
    ]
    fact = await actual_units(
        session, frozenset(people), day, day, [product_id] if product_id else None
    )
    names = dict((await session.execute(select(Product.id, Product.name))).all())
    points: dict[int, dict[str, Any]] = {}
    plan_total: dict[int, int] = defaultdict(int)
    fact_total: dict[int, int] = defaultdict(int)
    for v in views:
        u = people[v.shift.user_id]
        pt = points.setdefault(
            v.point.id, {"point": v.point.name, "address": v.point.address, "people": []}
        )
        plans = {pid: n for pid, n in v.plans.items() if not product_id or pid == product_id}
        facts = {p: n for (uid, _, p), n in fact.items() if uid == u.id}
        for pid, n in plans.items():
            plan_total[pid] += n
        for pid, n in facts.items():
            fact_total[pid] += n
        pt["people"].append(
            {
                "user_id": u.id,
                "full_name": u.full_name,
                "start": v.shift.start_time.strftime("%H:%M"),
                "end": v.shift.end_time.strftime("%H:%M"),
                "plans": [{"product": names.get(p), "units": n} for p, n in plans.items()],
                "facts": [{"product": names.get(p), "units": n} for p, n in facts.items()],
            }
        )
    result = {
        "day": day,
        "points": sorted(points.values(), key=lambda p: p["point"]),
        "people": len(views),
        "plan": [{"product": names.get(p), "units": n} for p, n in sorted(plan_total.items())],
        "fact": [{"product": names.get(p), "units": n} for p, n in sorted(fact_total.items())],
    }
    result["text"] = distribution_text(result)
    return result


def distribution_text(d: dict[str, Any]) -> str:
    day: date = d["day"]
    lines = [f"Распред на {day:%d.%m.%Y} ({WEEKDAYS[day.weekday()]})"]
    for pt in d["points"]:
        lines.append("")
        lines.append(pt["point"] + (f" ({pt['address']})" if pt["address"] else ""))
        for p in pt["people"]:
            plan = ", ".join(f"{x['product']} {x['units']}" for x in p["plans"]) or "без плана"
            lines.append(f"• {p['full_name']} {p['start']}–{p['end']} — {plan}")
    lines.append("")
    plan = ", ".join(f"{x['product']} {x['units']}" for x in d["plan"]) or "—"
    lines.append(f"Итого: людей {d['people']}; план: {plan}")
    if d["fact"]:
        lines.append("Факт: " + ", ".join(f"{x['product']} {x['units']}" for x in d["fact"]))
    return "\n".join(lines)


async def colleagues(session: AsyncSession, actor: Actor, start: date, end: date) -> dict[str, Any]:
    """График коллег по настройке владельца: только свой / занятость / подробно."""
    mode = await settings_svc.get(session, "schedule_visibility")
    if mode == "own" and not actor.is_owner:
        return {"mode": mode, "items": []}
    nets = select(UserNetwork.network_id).where(UserNetwork.user_id == actor.id)
    has_nets = await session.scalar(select(func.count()).select_from(nets.subquery()))
    q = select(User).where(User.status == AccountStatus.ACTIVE, User.id != actor.id)
    if has_nets:
        q = q.where(
            User.id.in_(select(UserNetwork.user_id).where(UserNetwork.network_id.in_(nets)))
        )
    people = {u.id: u for u in (await session.execute(q)).scalars()}
    views = await shifts_between(session, frozenset(people), start, end, (ShiftStatus.CONFIRMED,))
    items = []
    for v in views:
        item: dict[str, Any] = {"day": v.shift.day, "full_name": people[v.shift.user_id].full_name}
        if mode == "full" or actor.is_owner:
            item.update(
                start=v.shift.start_time.strftime("%H:%M"),
                end=v.shift.end_time.strftime("%H:%M"),
                point=v.point.name,
            )
        items.append(item)
    return {"mode": "full" if actor.is_owner else mode, "items": items}


async def pending_shifts(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    actor.require(Perm.SCHEDULE_MANAGE)
    scope = await scope_for(session, actor, Perm.SCHEDULE_MANAGE)
    q = (
        select(Shift, WorkPoint, User)
        .join(WorkPoint, WorkPoint.id == Shift.point_id)
        .join(User, User.id == Shift.user_id)
        .where(Shift.status == ShiftStatus.PENDING, Shift.day >= timeutil.msk_today())
        .order_by(Shift.day, Shift.start_time)
    )
    if scope is not ALL:
        q = q.where(Shift.user_id.in_(scope))
    out = []
    for s, p, u in await session.execute(q):
        out.append(
            {
                "id": s.id,
                "day": s.day,
                "start": s.start_time.strftime("%H:%M"),
                "end": s.end_time.strftime("%H:%M"),
                "point": p.name,
                "user_id": u.id,
                "full_name": u.full_name,
                "plans": await _plans_of(session, s.id),
            }
        )
    return out
