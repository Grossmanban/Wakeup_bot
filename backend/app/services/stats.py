"""Статистика и производительность с учётом качества (ТЗ 16).

Время выполнения задания — от нажатия «Найдено» до завершения (серверное время).
Пересекающиеся задания одного человека не суммируются: интервалы объединяются, поэтому
производительность не завышается. Если зафиксированного времени мало, показатели в час
не считаются («Недостаточно данных»).
"""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.errors import AppError, Forbidden
from app.models import (
    DONE_STATUSES,
    AccountStatus,
    EntryKind,
    LedgerEntry,
    Product,
    Shift,
    ShiftPlan,
    ShiftStatus,
    Task,
    TaskStatus,
    User,
    UserNetwork,
)
from app.permissions import Perm
from app.services import settings as settings_svc
from app.services.access import ALL, Actor, scope_for, team_of

APPROVED = (TaskStatus.APPROVED, TaskStatus.PAID)
# Меньше этого зафиксированного времени показатели «в час» не считаются.
MIN_HOURS = 0.25

FORMULAS = {
    "quality": "Качество = одобренные единицы / (одобренные + отклонённые) × 100%. "
    "Ожидающие модерации не учитываются.",
    "gross_per_hour": "Валовые единицы в час = выполненные единицы / зафиксированное время "
    "выполнения (от «Найдено» до завершения, пересекающиеся интервалы объединены).",
    "paid_per_hour": "Фактические оплачиваемые единицы в час = допущенные к оплате единицы / "
    "время выполнения именно этих заданий.",
    "forecast_paid_per_hour": "Прогноз оплачиваемых единиц в час = валовые единицы в час × доля "
    "одобрения (качество / 100).",
    "earnings_per_hour": "Заработок в час = заработок по допущенным заданиям / "
    "время их выполнения.",
    "hourly": "Почасовое распределение: единица относится к часу завершения задания (МСК).",
}


def merged_hours(intervals: list[tuple[datetime, datetime]]) -> float:
    """Суммарная длительность объединения интервалов, часов."""
    total = 0.0
    cur_s = cur_e = None
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total / 3600


@dataclass
class Bucket:
    """Накопитель показателей для сотрудника, продукта или итога."""

    tasks: int = 0
    units: int = 0
    approved: int = 0
    rejected: int = 0
    pending: int = 0
    paid: int = 0
    days: set = field(default_factory=set)
    intervals: list = field(default_factory=list)
    approved_intervals: list = field(default_factory=list)
    durations: list = field(default_factory=list)
    earnings: int = 0
    planned: int = 0

    def add(self, t: Task) -> None:
        self.tasks += 1
        self.units += t.units
        self.days.add(timeutil.msk_date(t.completed_at))
        if t.status in APPROVED:
            self.approved += t.units
        if t.status == TaskStatus.PAID:
            self.paid += t.units
        if t.status == TaskStatus.REJECTED:
            self.rejected += t.units
        if t.status == TaskStatus.MODERATION:
            self.pending += t.units
        if t.found_at and t.completed_at > t.found_at:
            iv = (t.found_at, t.completed_at)
            self.intervals.append(iv)
            self.durations.append((t.completed_at - t.found_at).total_seconds())
            if t.status in APPROVED:
                self.approved_intervals.append(iv)

    def result(self, min_sample: int) -> dict[str, Any]:
        hours = merged_hours(self.intervals)
        approved_hours = merged_hours(self.approved_intervals)
        reviewed = self.approved + self.rejected
        quality = self.approved * 100 / reviewed if reviewed else None
        enough_time = hours >= MIN_HOURS
        gross = self.units / hours if enough_time else None
        paid_rate = self.approved / approved_hours if approved_hours >= MIN_HOURS else None
        forecast = gross * quality / 100 if gross is not None and quality is not None else None
        r = lambda x, n=2: None if x is None else round(x, n)  # noqa: E731
        return {
            "tasks": self.tasks,
            "units": self.units,
            "working_days": len(self.days),
            "hours": r(hours),
            "units_per_hour": r(gross),
            "units_per_day": r(self.units / len(self.days)) if self.days else None,
            "avg_task_minutes": r(sum(self.durations) / len(self.durations) / 60, 1)
            if self.durations
            else None,
            "pending_units": self.pending,
            "approved_units": self.approved,
            "paid_units": self.paid,
            "rejected_units": self.rejected,
            "quality": r(quality, 1),
            "sample_units": reviewed,
            "small_sample": reviewed < min_sample,
            "paid_units_per_hour": r(paid_rate),
            "forecast_paid_units_per_hour": r(forecast),
            "earnings_kop": self.earnings,
            "earnings_per_hour_kop": round(self.earnings / approved_hours)
            if approved_hours >= MIN_HOURS
            else None,
            "planned_units": self.planned,
            "plan_percent": r(self.units * 100 / self.planned, 1) if self.planned else None,
            "insufficient_time": not enough_time,
        }


@dataclass
class Filters:
    start: date
    end: date
    user_ids: list[int] | None = None
    network_id: int | None = None
    coach_id: int | None = None
    instructor_id: int | None = None
    product_ids: list[int] | None = None
    statuses: list[TaskStatus] | None = None


async def _people(session: AsyncSession, actor: Actor, f: Filters, *, self_only: bool) -> set[int]:
    if self_only:
        return {actor.id}
    if not actor.has(Perm.STATS_VIEW):
        raise Forbidden("no_permission", "Недостаточно прав для просмотра статистики")
    scope = await scope_for(session, actor, Perm.STATS_VIEW)
    q = select(User.id).where(
        User.status.in_([AccountStatus.ACTIVE, AccountStatus.DISABLED, AccountStatus.BLOCKED])
    )
    if scope is not ALL:
        q = q.where(User.id.in_(scope))
    if f.user_ids:
        q = q.where(User.id.in_(f.user_ids))
    if f.network_id:
        q = q.where(
            User.id.in_(select(UserNetwork.user_id).where(UserNetwork.network_id == f.network_id))
        )
    for lead in (f.coach_id, f.instructor_id):
        if lead:
            q = q.where(User.id.in_(await team_of(session, lead) or {0}))
    return set((await session.execute(q)).scalars())


async def _tasks(session: AsyncSession, people: set[int], f: Filters) -> list[Task]:
    if (f.end - f.start).days > 366:
        raise AppError("bad_period", "Период — не больше года")
    lo, hi = timeutil.msk_day_bounds(f.start, f.end)
    q = select(Task).where(
        Task.assignee_id.in_(people or {0}),
        Task.completed_at >= lo,
        Task.completed_at < hi,
        Task.status.in_(f.statuses or DONE_STATUSES),
    )
    if f.product_ids:
        q = q.where(Task.product_id.in_(f.product_ids))
    return list((await session.execute(q.order_by(Task.completed_at))).scalars())


async def _earnings(session: AsyncSession, task_ids: list[int]) -> dict[int, int]:
    if not task_ids:
        return {}
    rows = await session.execute(
        select(LedgerEntry.task_id, func.sum(LedgerEntry.amount_kop))
        .where(
            LedgerEntry.task_id.in_(task_ids),
            LedgerEntry.kind.in_([EntryKind.EARNING, EntryKind.EARNING_REVERSAL]),
        )
        .group_by(LedgerEntry.task_id)
    )
    return {t: int(a) for t, a in rows}


async def _plans(session: AsyncSession, people: set[int], f: Filters) -> dict[tuple[int, int], int]:
    q = (
        select(Shift.user_id, ShiftPlan.product_id, func.sum(ShiftPlan.units))
        .join(ShiftPlan, ShiftPlan.shift_id == Shift.id)
        .where(
            Shift.user_id.in_(people or {0}),
            Shift.day >= f.start,
            Shift.day <= f.end,
            Shift.status == ShiftStatus.CONFIRMED,
        )
        .group_by(Shift.user_id, ShiftPlan.product_id)
    )
    if f.product_ids:
        q = q.where(ShiftPlan.product_id.in_(f.product_ids))
    return {(u, p): int(n) for u, p, n in await session.execute(q)}


async def _shift_hours(session: AsyncSession, people: set[int], f: Filters) -> dict[int, float]:
    rows = await session.execute(
        select(Shift.user_id, Shift.start_time, Shift.end_time).where(
            Shift.user_id.in_(people or {0}),
            Shift.day >= f.start,
            Shift.day <= f.end,
            Shift.status == ShiftStatus.CONFIRMED,
        )
    )
    out: dict[int, float] = defaultdict(float)
    for uid, s, e in rows:
        out[uid] += (
            datetime.combine(date.min, e) - datetime.combine(date.min, s)
        ).total_seconds() / 3600
    return out


async def compute(
    session: AsyncSession, actor: Actor, f: Filters, *, self_only: bool = False
) -> dict[str, Any]:
    people = await _people(session, actor, f, self_only=self_only)
    tasks = await _tasks(session, people, f)
    earn = await _earnings(session, [t.id for t in tasks])
    plans = await _plans(session, people, f)
    shift_hours = await _shift_hours(session, people, f)
    names = {
        u.id: u.full_name
        for u in (await session.execute(select(User).where(User.id.in_(people or {0})))).scalars()
    }
    products = {p.id: p for p in (await session.execute(select(Product))).scalars()}
    default_sample = await settings_svc.get(session, "min_sample_units")

    by_user: dict[int, Bucket] = defaultdict(Bucket)
    by_user_product: dict[tuple[int, int], Bucket] = defaultdict(Bucket)
    by_product: dict[int, Bucket] = defaultdict(Bucket)
    total = Bucket()
    hourly: dict[int, int] = defaultdict(int)
    for t in tasks:
        for b in (
            by_user[t.assignee_id],
            by_user_product[(t.assignee_id, t.product_id)],
            by_product[t.product_id],
            total,
        ):
            b.add(t)
            b.earnings += earn.get(t.id, 0)
        hourly[timeutil.to_msk(t.completed_at).hour] += t.units
    for (uid, pid), n in plans.items():
        by_user[uid].planned += n
        by_user_product[(uid, pid)].planned += n
        by_product[pid].planned += n
        total.planned += n

    def sample_for(pids: list[int]) -> int:
        vals = [products[p].min_sample_units for p in pids if p in products]
        return max(vals) if vals else default_sample

    users = []
    for uid in sorted(people, key=lambda u: names.get(u, "")):
        b = by_user.get(uid, Bucket())
        pids = sorted({p for (u, p) in by_user_product if u == uid})
        users.append(
            {
                "user_id": uid,
                "full_name": names.get(uid, f"#{uid}"),
                **b.result(sample_for(pids)),
                "shift_hours": round(shift_hours.get(uid, 0), 2),
                "products": [
                    {
                        "product_id": p,
                        "product": products[p].name,
                        **by_user_product[(uid, p)].result(products[p].min_sample_units),
                    }
                    for p in pids
                ],
            }
        )
    return {
        "period": {"start": f.start, "end": f.end},
        "total": total.result(sample_for(list(by_product))),
        "users": users,
        "products": [
            {"product_id": p, "product": products[p].name, **b.result(products[p].min_sample_units)}
            for p, b in sorted(by_product.items(), key=lambda x: products[x[0]].name)
        ],
        "hourly": [{"hour": h, "units": hourly.get(h, 0)} for h in range(24)],
        "formulas": FORMULAS,
    }


SORT_KEYS = {
    "units": "units",
    "quality": "quality",
    "units_per_hour": "units_per_hour",
    "paid_units_per_hour": "paid_units_per_hour",
    "earnings": "earnings_kop",
    "plan": "plan_percent",
}


def sort_users(users: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    field_name = SORT_KEYS.get(key, "units")
    return sorted(
        users, key=lambda u: (u[field_name] is None, -(u[field_name] or 0), u["full_name"])
    )


async def drilldown(
    session: AsyncSession, actor: Actor, f: Filters, *, self_only: bool = False, limit: int = 1000
) -> list[dict[str, Any]]:
    """Задания, из которых сложились показатели (ТЗ 16.7)."""
    people = await _people(session, actor, f, self_only=self_only)
    tasks = (await _tasks(session, people, f))[:limit]
    earn = await _earnings(session, [t.id for t in tasks])
    names = {
        u.id: u.full_name
        for u in (await session.execute(select(User).where(User.id.in_(people or {0})))).scalars()
    }
    pnames = dict((await session.execute(select(Product.id, Product.name))).all())
    return [
        {
            "task_id": t.id,
            "external_id": t.external_id,
            "product": pnames.get(t.product_id),
            "employee": names.get(t.assignee_id),
            "found_at": t.found_at,
            "completed_at": t.completed_at,
            "units": t.units,
            "minutes": round((t.completed_at - t.found_at).total_seconds() / 60, 1)
            if t.found_at
            else None,
            "status": t.status.value,
            "earnings_kop": earn.get(t.id, 0),
        }
        for t in tasks
    ]


STATUS_RU = {
    "moderation": "На модерации",
    "approved": "Допущено к оплате",
    "rejected": "Отклонено",
    "paid": "Оплачено",
}


def export(
    rows: list[dict[str, Any]], columns: list[tuple[str, str]], fmt: str
) -> tuple[bytes, str]:
    def cell(v: Any) -> Any:
        if isinstance(v, datetime):
            return timeutil.to_msk(v).strftime("%d.%m.%Y %H:%M")
        if isinstance(v, date):
            return v.strftime("%d.%m.%Y")
        return v

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow([title for _, title in columns])
        for r in rows:
            w.writerow([cell(r.get(k)) for k, _ in columns])
        # BOM — чтобы Excel открыл кириллицу в UTF-8.
        return ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8"
    wb = Workbook()
    ws = wb.active
    ws.append([title for _, title in columns])
    for r in rows:
        ws.append([cell(r.get(k)) for k, _ in columns])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = max(
            10, min(40, max(len(str(c.value or "")) for c in col) + 2)
        )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


TASK_COLUMNS = [
    ("task_id", "ID"),
    ("external_id", "ID задания"),
    ("product", "Продукт"),
    ("employee", "Сотрудник"),
    ("found_at", "Найдено (МСК)"),
    ("completed_at", "Завершено (МСК)"),
    ("units", "Единиц"),
    ("minutes", "Длительность, мин"),
    ("status", "Статус модерации"),
    ("earnings_rub", "Заработок, ₽"),
]

USER_COLUMNS = [
    ("full_name", "Сотрудник"),
    ("units", "Выполнено единиц"),
    ("tasks", "Заданий"),
    ("working_days", "Рабочих дней"),
    ("hours", "Время выполнения, ч"),
    ("shift_hours", "Часы смен"),
    ("units_per_hour", "Единиц в час"),
    ("units_per_day", "Единиц в день"),
    ("avg_task_minutes", "Среднее задание, мин"),
    ("pending_units", "На модерации"),
    ("approved_units", "Допущено к оплате"),
    ("paid_units", "Оплачено"),
    ("rejected_units", "Отклонено"),
    ("quality", "Качество, %"),
    ("sample_units", "Выборка, ед."),
    ("paid_units_per_hour", "Оплачиваемых ед./ч (факт)"),
    ("forecast_paid_units_per_hour", "Оплачиваемых ед./ч (прогноз)"),
    ("earnings_rub", "Заработок, ₽"),
    ("plan_percent", "Выполнение плана, %"),
]


def with_rub(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **r,
            "earnings_rub": round(r.get("earnings_kop", 0) / 100, 2),
            "status": STATUS_RU.get(r.get("status"), r.get("status")),
        }
        for r in rows
    ]


def period_preset(name: str, today: date | None = None) -> tuple[date, date]:
    """Пресеты периода по МСК (ТЗ 16.1)."""
    today = today or timeutil.msk_today()
    ws = timeutil.week_start(today)
    first = today.replace(day=1)
    match name:
        case "today":
            return today, today
        case "yesterday":
            return today - timedelta(days=1), today - timedelta(days=1)
        case "week":
            return ws, today
        case "last_week":
            return ws - timedelta(days=7), ws - timedelta(days=1)
        case "month":
            return first, today
        case "last_month":
            last = first - timedelta(days=1)
            return last.replace(day=1), last
    raise AppError("bad_period", "Неизвестный период")
