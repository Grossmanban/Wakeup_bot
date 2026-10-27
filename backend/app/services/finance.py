"""Финансовый журнал: начисления, сторно, штрафы, бонусы, «налог на лень», выплаты (ТЗ 11).

Принципы:
- деньги — целые копейки;
- проведённые записи не меняются, исправление — новая запись со ссылкой на исходную;
- у каждой операции ключ идемпотентности (dedup_key / idempotency_key), повтор не дублирует;
- выплата сериализуется блокировкой строки сотрудника, сумма не бывает отрицательной.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from html import escape
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.errors import AppError, Conflict, NotFound
from app.models import (
    DONE_STATUSES,
    BonusRule,
    EntryKind,
    LazyConsumption,
    LazyExemption,
    LazyRule,
    LazyUnits,
    LedgerEntry,
    Payout,
    PayoutAllocation,
    Product,
    Shift,
    ShiftPlan,
    ShiftStatus,
    Task,
    TaskStatus,
    User,
)
from app.money import rub
from app.permissions import Perm
from app.services import audit, outbox
from app.services import products as products_svc
from app.services.access import ALL, Actor, get_target, scope_for
from app.services.audit import Category
from app.services.tasks import add_event

APPROVED = (TaskStatus.APPROVED, TaskStatus.PAID)

KIND_TITLES = {
    EntryKind.EARNING: "Заработок",
    EntryKind.EARNING_REVERSAL: "Отмена заработка",
    EntryKind.PENALTY: "Штраф",
    EntryKind.BONUS: "Бонус",
    EntryKind.BONUS_REVERSAL: "Отмена бонуса",
    EntryKind.REFERRAL_BONUS: "Реферальный бонус",
    EntryKind.ADJUSTMENT: "Корректировка",
    EntryKind.OFFSET: "Взаимозачёт",
    EntryKind.LAZY_ADJUSTMENT: "Сниженная ставка",
}


async def _notify(session: AsyncSession, user_id: int, key: str, text: str) -> None:
    user = await session.get(User, user_id)
    await outbox.enqueue(
        session, dedup_key=key, chat_id=user.telegram_id, related_user_id=user.id, text=text
    )


# --- заработок по модерации ---------------------------------------------------------


async def on_moderation_changed(
    session: AsyncSession,
    task: Task,
    product: Product,
    old: TaskStatus,
    new: TaskStatus,
    actor_id: int,
) -> None:
    """Допуск к оплате начисляет заработок по ставке на момент решения.

    Если решение потом меняется, исходная запись не трогается — создаётся сторно.
    """
    if new == TaskStatus.APPROVED and old not in APPROVED:
        user = await session.get(User, task.assignee_id)
        # Без настроенной ставки решение не проводится: ноль не подставляется.
        rate = await products_svc.resolve_rate(session, user, product.id)
        n = await session.scalar(
            select(func.count()).where(
                LedgerEntry.task_id == task.id, LedgerEntry.kind == EntryKind.EARNING
            )
        )
        session.add(
            LedgerEntry(
                user_id=user.id,
                kind=EntryKind.EARNING,
                product_id=product.id,
                task_id=task.id,
                units=task.units,
                rate_kop=rate,
                amount_kop=task.units * rate,
                occurred_at=task.completed_at,
                author_id=actor_id,
                dedup_key=f"earning:{task.id}:{n + 1}",
            )
        )
        await session.flush()
        await evaluate_bonus_rules(session, user.id, timeutil.msk_date(task.completed_at))
    elif old in APPROVED and new not in APPROVED:
        source = await _unreversed_earning(session, task.id)
        if source is not None:
            session.add(
                LedgerEntry(
                    user_id=source.user_id,
                    kind=EntryKind.EARNING_REVERSAL,
                    product_id=source.product_id,
                    task_id=task.id,
                    units=-(source.units or 0),
                    rate_kop=source.rate_kop,
                    amount_kop=-source.amount_kop,
                    occurred_at=source.occurred_at,
                    author_id=actor_id,
                    reason=task.moderation_reason,
                    source_entry_id=source.id,
                    dedup_key=f"reversal:{source.id}",
                )
            )
    await session.flush()


async def _unreversed_earning(session: AsyncSession, task_id: int) -> LedgerEntry | None:
    reversed_ids = select(LedgerEntry.source_entry_id).where(
        LedgerEntry.task_id == task_id,
        LedgerEntry.kind == EntryKind.EARNING_REVERSAL,
        LedgerEntry.source_entry_id.is_not(None),
    )
    return await session.scalar(
        select(LedgerEntry)
        .where(
            LedgerEntry.task_id == task_id,
            LedgerEntry.kind == EntryKind.EARNING,
            LedgerEntry.id.not_in(reversed_ids),
        )
        .order_by(LedgerEntry.id.desc())
        .limit(1)
    )


# --- ручные операции ----------------------------------------------------------------


async def _existing(session: AsyncSession, key: str) -> LedgerEntry | None:
    return await session.scalar(select(LedgerEntry).where(LedgerEntry.dedup_key == key))


async def add_penalty(
    session: AsyncSession,
    actor: Actor,
    user_id: int,
    *,
    reason: str,
    units: int | None = None,
    product_id: int | None = None,
    amount_kop: int | None = None,
    occurred_on: date | None = None,
    request_key: str,
) -> LedgerEntry:
    """Штраф в единицах (по ставке продукта на момент штрафа) или в рублях."""
    user = await get_target(session, actor, Perm.FINANCE_MANAGE, user_id)
    key = f"penalty:{actor.id}:{request_key}"
    if (done := await _existing(session, key)) is not None:
        return done
    reason = reason.strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину штрафа")
    rate = None
    if units is not None:
        if product_id is None or units <= 0:
            raise AppError("bad_penalty", "Для штрафа в единицах укажите продукт и число единиц")
        await products_svc.get_product(session, product_id)
        rate = await products_svc.resolve_rate(session, user, product_id)
        amount_kop = units * rate
    if not amount_kop or amount_kop <= 0:
        raise AppError("bad_penalty", "Сумма штрафа должна быть больше нуля")
    entry = LedgerEntry(
        user_id=user.id,
        kind=EntryKind.PENALTY,
        product_id=product_id,
        units=units,
        rate_kop=rate,
        amount_kop=-amount_kop,
        occurred_at=timeutil.msk_at(occurred_on) if occurred_on else timeutil.now_utc(),
        author_id=actor.id,
        reason=reason,
        dedup_key=key,
    )
    session.add(entry)
    await session.flush()
    audit.record(
        session,
        category=Category.PENALTIES,
        action="penalty.created",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="ledger_entry",
        object_id=entry.id,
        new={"amount_kop": -amount_kop, "units": units, "product_id": product_id, "rate_kop": rate},
        reason=reason,
    )
    what = f"{units} ед. × {rub(rate)} = {rub(amount_kop)}" if units else rub(amount_kop)
    await _notify(
        session,
        user.id,
        f"ledger:{entry.id}",
        f"Назначен штраф: {what}.\nПричина: {escape(reason)}",
    )
    await session.commit()
    return entry


async def add_bonus(
    session: AsyncSession,
    actor: Actor,
    user_id: int,
    *,
    amount_kop: int,
    reason: str,
    occurred_on: date | None,
    request_key: str,
) -> LedgerEntry:
    user = await get_target(session, actor, Perm.FINANCE_MANAGE, user_id)
    key = f"bonus:{actor.id}:{request_key}"
    if (done := await _existing(session, key)) is not None:
        return done
    reason = reason.strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину бонуса")
    if amount_kop <= 0:
        raise AppError("bad_amount", "Сумма бонуса должна быть больше нуля")
    entry = LedgerEntry(
        user_id=user.id,
        kind=EntryKind.BONUS,
        amount_kop=amount_kop,
        occurred_at=timeutil.msk_at(occurred_on) if occurred_on else timeutil.now_utc(),
        author_id=actor.id,
        reason=reason,
        dedup_key=key,
    )
    session.add(entry)
    await session.flush()
    audit.record(
        session,
        category=Category.BONUSES,
        action="bonus.created",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="ledger_entry",
        object_id=entry.id,
        new={"amount_kop": amount_kop},
        reason=reason,
    )
    await _notify(
        session,
        user.id,
        f"ledger:{entry.id}",
        f"Начислен бонус {rub(amount_kop)}.\n{escape(reason)}",
    )
    await session.commit()
    return entry


async def add_adjustment(
    session: AsyncSession,
    actor: Actor,
    user_id: int,
    *,
    amount_kop: int,
    reason: str,
    kind: EntryKind,
    source_entry_id: int | None,
    request_key: str,
) -> LedgerEntry:
    """Корректировка или взаимозачёт: сумма со знаком, причина обязательна."""
    if kind not in (EntryKind.ADJUSTMENT, EntryKind.OFFSET):
        raise AppError("bad_kind", "Недопустимый тип операции")
    user = await get_target(session, actor, Perm.FINANCE_MANAGE, user_id)
    key = f"adj:{actor.id}:{request_key}"
    if (done := await _existing(session, key)) is not None:
        return done
    reason = reason.strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину")
    if amount_kop == 0:
        raise AppError("bad_amount", "Сумма не может быть нулевой")
    if source_entry_id is not None:
        src = await session.get(LedgerEntry, source_entry_id)
        if src is None or src.user_id != user.id:
            raise NotFound("Исходная операция не найдена")
    entry = LedgerEntry(
        user_id=user.id,
        kind=kind,
        amount_kop=amount_kop,
        occurred_at=timeutil.now_utc(),
        author_id=actor.id,
        reason=reason,
        source_entry_id=source_entry_id,
        dedup_key=key,
    )
    session.add(entry)
    await session.flush()
    audit.record(
        session,
        category=Category.FINANCE,
        action=f"{kind.value}.created",
        actor_id=actor.id,
        target_user_id=user.id,
        object_type="ledger_entry",
        object_id=entry.id,
        new={"amount_kop": amount_kop, "source_entry_id": source_entry_id},
        reason=reason,
    )
    await _notify(
        session,
        user.id,
        f"ledger:{entry.id}",
        f"{KIND_TITLES[kind]}: {rub(amount_kop)}.\n{escape(reason)}",
    )
    await session.commit()
    return entry


async def cancel_bonus(
    session: AsyncSession, actor: Actor, entry_id: int, reason: str
) -> LedgerEntry:
    """Отмена бонуса — отдельной записью; если бонус уже выплачен, отмена становится долгом."""
    entry = await session.get(LedgerEntry, entry_id, with_for_update=True)
    if entry is None or entry.kind not in (EntryKind.BONUS, EntryKind.REFERRAL_BONUS):
        raise NotFound("Бонус не найден")
    await get_target(session, actor, Perm.FINANCE_MANAGE, entry.user_id)
    reason = reason.strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину отмены")
    key = f"bonus_reversal:{entry.id}"
    if (done := await _existing(session, key)) is not None:
        return done
    rev = LedgerEntry(
        user_id=entry.user_id,
        kind=EntryKind.BONUS_REVERSAL,
        amount_kop=-entry.amount_kop,
        occurred_at=entry.occurred_at,
        author_id=actor.id,
        reason=reason,
        source_entry_id=entry.id,
        bonus_rule_id=entry.bonus_rule_id,
        dedup_key=key,
    )
    session.add(rev)
    await session.flush()
    audit.record(
        session,
        category=Category.BONUSES,
        action="bonus.cancelled",
        actor_id=actor.id,
        target_user_id=entry.user_id,
        object_type="ledger_entry",
        object_id=entry.id,
        old={"amount_kop": entry.amount_kop},
        new={"reversal_id": rev.id},
        reason=reason,
    )
    await session.commit()
    return rev


# --- автоматические бонусы ----------------------------------------------------------


def _validate_rule(data: dict[str, Any]) -> None:
    if data["amount_kop"] <= 0:
        raise AppError("bad_amount", "Сумма бонуса должна быть больше нуля")
    if not data["conditions"]:
        raise AppError("bad_rule", "Добавьте хотя бы одно условие по продукту")
    if data["period_to"] < data["period_from"]:
        raise AppError("bad_period", "Конец периода раньше начала")
    mq = data.get("min_quality")
    if mq is not None and not 0 <= mq <= 100:
        raise AppError("bad_rule", "Качество — от 0 до 100%")


async def save_bonus_rule(
    session: AsyncSession, actor: Actor, rule_id: int | None, data: dict[str, Any]
) -> BonusRule:
    actor.require(Perm.FINANCE_MANAGE)
    _validate_rule(data)
    for c in data["conditions"]:
        await products_svc.get_product(session, c["product_id"])
    if rule_id is None:
        rule = BonusRule(created_by_id=actor.id, **data)
        session.add(rule)
        await session.flush()
        old = None
    else:
        rule = await session.get(BonusRule, rule_id, with_for_update=True)
        if rule is None:
            raise NotFound("Правило не найдено")
        old = {k: getattr(rule, k) for k in data}
        for k, v in data.items():
            setattr(rule, k, v)
    audit.record(
        session,
        category=Category.BONUSES,
        action="bonus_rule.saved",
        actor_id=actor.id,
        object_type="bonus_rule",
        object_id=rule.id,
        old=old,
        new=data,
    )
    await session.commit()
    return rule


async def _approved_units_by_product(
    session: AsyncSession, user_id: int, start: date, end: date, product_ids: list[int]
) -> tuple[dict[int, int], int, int]:
    lo, hi = timeutil.msk_day_bounds(start, end)
    rows = await session.execute(
        select(Task.product_id, Task.status, func.sum(Task.units))
        .where(
            Task.assignee_id == user_id,
            Task.product_id.in_(product_ids),
            Task.completed_at >= lo,
            Task.completed_at < hi,
            Task.status.in_([*APPROVED, TaskStatus.REJECTED]),
        )
        .group_by(Task.product_id, Task.status)
    )
    approved: dict[int, int] = defaultdict(int)
    a_total = r_total = 0
    for pid, st, n in rows:
        if st in APPROVED:
            approved[pid] += int(n)
            a_total += int(n)
        else:
            r_total += int(n)
    return approved, a_total, r_total


async def evaluate_bonus_rules(
    session: AsyncSession, user_id: int, day: date | None = None
) -> list[LedgerEntry]:
    """Начислить автоматические бонусы, условия которых выполнены. Каждое правило — один раз."""
    q = select(BonusRule).where(BonusRule.is_active.is_(True))
    if day is not None:
        q = q.where(BonusRule.period_from <= day, BonusRule.period_to >= day)
    created = []
    for rule in (await session.execute(q)).scalars():
        key = f"bonus_rule:{rule.id}:{user_id}"
        if await _existing(session, key) is not None:
            continue
        pids = [c["product_id"] for c in rule.conditions]
        approved, a, r = await _approved_units_by_product(
            session, user_id, rule.period_from, rule.period_to, pids
        )
        if any(approved.get(c["product_id"], 0) < c["min_units"] for c in rule.conditions):
            continue
        if rule.min_quality is not None and (a + r == 0 or a * 100 < rule.min_quality * (a + r)):
            continue
        entry = LedgerEntry(
            user_id=user_id,
            kind=EntryKind.BONUS,
            amount_kop=rule.amount_kop,
            occurred_at=timeutil.now_utc(),
            reason=f"Автоматический бонус «{rule.name}»",
            bonus_rule_id=rule.id,
            dedup_key=key,
        )
        session.add(entry)
        await session.flush()
        audit.record(
            session,
            category=Category.BONUSES,
            action="bonus.auto",
            actor_id=None,
            target_user_id=user_id,
            object_type="bonus_rule",
            object_id=rule.id,
            new={"amount_kop": rule.amount_kop, "entry_id": entry.id},
        )
        await _notify(
            session,
            user_id,
            f"ledger:{entry.id}",
            f"Начислен бонус «{escape(rule.name)}»: {rub(rule.amount_kop)}",
        )
        created.append(entry)
    return created


# --- «налог на лень» ----------------------------------------------------------------


async def save_lazy_rule(
    session: AsyncSession, actor: Actor, rule_id: int | None, data: dict[str, Any]
) -> LazyRule:
    actor.require(Perm.FINANCE_MANAGE)
    await products_svc.get_product(session, data["product_id"])
    if data["min_daily_units"] <= 0 or data["reduced_rate_kop"] < 0:
        raise AppError("bad_rule", "Минимум — больше нуля, ставка — не отрицательная")
    if data.get("period_to") and data["period_to"] < data["period_from"]:
        raise AppError("bad_period", "Конец периода раньше начала")
    if rule_id is None:
        rule = LazyRule(created_by_id=actor.id, **data)
        session.add(rule)
        await session.flush()
        old = None
    else:
        rule = await session.get(LazyRule, rule_id, with_for_update=True)
        if rule is None:
            raise NotFound("Правило не найдено")
        old = {k: getattr(rule, k) for k in data}
        for k, v in data.items():
            setattr(rule, k, v)
    audit.record(
        session,
        category=Category.FINANCE,
        action="lazy_rule.saved",
        actor_id=actor.id,
        object_type="lazy_rule",
        object_id=rule.id,
        old=old,
        new=data,
    )
    await session.commit()
    return rule


async def set_lazy_exemption(
    session: AsyncSession, actor: Actor, rule_id: int, user_id: int, on: bool
) -> None:
    await get_target(session, actor, Perm.FINANCE_MANAGE, user_id)
    rule = await session.get(LazyRule, rule_id)
    if rule is None:
        raise NotFound("Правило не найдено")
    row = await session.get(LazyExemption, (rule_id, user_id))
    if on and row is None:
        session.add(LazyExemption(rule_id=rule_id, user_id=user_id, created_by_id=actor.id))
    elif not on and row is not None:
        await session.delete(row)
    else:
        return
    audit.record(
        session,
        category=Category.FINANCE,
        action="lazy_exemption.set" if on else "lazy_exemption.cleared",
        actor_id=actor.id,
        target_user_id=user_id,
        object_type="lazy_rule",
        object_id=rule_id,
    )
    await session.commit()


async def close_lazy_day(session: AsyncSession, day: date) -> int:
    """Записать недобор дня по каждому правилу. Повторный запуск не создаёт дублей.

    Правило применяется к сотруднику в день, когда он работал по продукту: была подтверждённая
    смена с планом по этому продукту или выполнена хотя бы одна единица (docs/decisions.md).
    """
    rules = (
        (
            await session.execute(
                select(LazyRule).where(
                    LazyRule.is_active.is_(True),
                    LazyRule.period_from <= day,
                    (LazyRule.period_to.is_(None)) | (LazyRule.period_to >= day),
                )
            )
        )
        .scalars()
        .all()
    )
    lo, hi = timeutil.msk_day_bounds(day, day)
    created = 0
    for rule in rules:
        done = dict(
            (
                await session.execute(
                    select(Task.assignee_id, func.sum(Task.units))
                    .where(
                        Task.product_id == rule.product_id,
                        Task.completed_at >= lo,
                        Task.completed_at < hi,
                        Task.status.in_(DONE_STATUSES),
                    )
                    .group_by(Task.assignee_id)
                )
            ).all()
        )
        planned = set(
            (
                await session.execute(
                    select(Shift.user_id)
                    .join(ShiftPlan, ShiftPlan.shift_id == Shift.id)
                    .where(
                        Shift.day == day,
                        Shift.status == ShiftStatus.CONFIRMED,
                        ShiftPlan.product_id == rule.product_id,
                    )
                )
            ).scalars()
        )
        exempt = set(
            (
                await session.execute(
                    select(LazyExemption.user_id).where(LazyExemption.rule_id == rule.id)
                )
            ).scalars()
        )
        candidates = (set(done) | planned) - exempt
        if not candidates:
            continue
        users = (await session.execute(select(User).where(User.id.in_(candidates)))).scalars()
        for u in users:
            # Без ролей и сотрудников в правиле — применяется ко всем; иначе к перечисленным.
            targeted = rule.roles or rule.user_ids
            if targeted and u.role.value not in rule.roles and u.id not in rule.user_ids:
                continue
            n = int(done.get(u.id, 0))
            if n >= rule.min_daily_units:
                continue
            exists = await session.scalar(
                select(LazyUnits.id).where(
                    LazyUnits.rule_id == rule.id, LazyUnits.user_id == u.id, LazyUnits.day == day
                )
            )
            if exists:
                continue
            row = LazyUnits(
                rule_id=rule.id,
                user_id=u.id,
                product_id=rule.product_id,
                day=day,
                done_units=n,
                units=rule.min_daily_units - n,
                reduced_rate_kop=rule.reduced_rate_kop,
            )
            session.add(row)
            await session.flush()
            audit.record(
                session,
                category=Category.FINANCE,
                action="lazy_units.accrued",
                actor_id=None,
                target_user_id=u.id,
                object_type="lazy_units",
                object_id=row.id,
                new={
                    "day": day.isoformat(),
                    "done": n,
                    "min": rule.min_daily_units,
                    "units": row.units,
                },
            )
            created += 1
    await session.commit()
    return created


async def lazy_balance(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    """Накопленные единицы по сниженной ставке: начислено, учтено в выплатах, остаток (FIFO)."""
    rows = (
        (
            await session.execute(
                select(LazyUnits)
                .where(LazyUnits.user_id == user_id)
                .order_by(LazyUnits.day, LazyUnits.id)
            )
        )
        .scalars()
        .all()
    )
    used = dict(
        (
            await session.execute(
                select(LazyConsumption.lazy_id, func.sum(LazyConsumption.units))
                .join(LazyUnits, LazyUnits.id == LazyConsumption.lazy_id)
                .where(LazyUnits.user_id == user_id)
                .group_by(LazyConsumption.lazy_id)
            )
        ).all()
    )
    return [
        {
            "id": r.id,
            "product_id": r.product_id,
            "day": r.day,
            "done_units": r.done_units,
            "units": r.units,
            "used": int(used.get(r.id, 0)),
            "left": r.units - int(used.get(r.id, 0)),
            "reduced_rate_kop": r.reduced_rate_kop,
        }
        for r in rows
    ]


# --- выплаты ------------------------------------------------------------------------


@dataclass
class PayoutCalc:
    user_id: int
    period_from: date
    period_to: date
    credits: list[LedgerEntry] = field(default_factory=list)
    debts: list[tuple[LedgerEntry, int]] = field(default_factory=list)  # (запись, остаток)
    allocations: list[tuple[int, int]] = field(default_factory=list)  # (entry_id, сумма)
    lazy: list[dict[str, Any]] = field(default_factory=list)
    gross: int = 0
    lazy_total: int = 0
    deduction: int = 0
    total: int = 0
    debt_left: int = 0

    def snapshot(self, names: dict[int, str]) -> dict[str, Any]:
        by_product: dict[str, dict[str, Any]] = {}
        for e in self.credits:
            if e.kind != EntryKind.EARNING:
                continue
            p = by_product.setdefault(
                str(e.product_id),
                {"product": names.get(e.product_id), "units": 0, "amount_kop": 0, "rates": set()},
            )
            p["units"] += e.units or 0
            p["amount_kop"] += e.amount_kop
            p["rates"].add(e.rate_kop)
        for p in by_product.values():
            p["rates"] = sorted(p["rates"])
        lazy_units = defaultdict(int)
        for c in self.lazy:
            lazy_units[str(c["product_id"])] += c["units"]
        for pid, p in by_product.items():
            p["reduced_units"] = lazy_units.get(pid, 0)
            p["normal_units"] = p["units"] - p["reduced_units"]
        return {
            "period_from": self.period_from.isoformat(),
            "period_to": self.period_to.isoformat(),
            "products": list(by_product.values()),
            "earnings_kop": sum(e.amount_kop for e in self.credits if e.kind == EntryKind.EARNING),
            "bonuses_kop": sum(
                e.amount_kop
                for e in self.credits
                if e.kind in (EntryKind.BONUS, EntryKind.REFERRAL_BONUS)
            ),
            "other_credits_kop": sum(
                e.amount_kop
                for e in self.credits
                if e.kind not in (EntryKind.EARNING, EntryKind.BONUS, EntryKind.REFERRAL_BONUS)
            ),
            "gross_kop": self.gross,
            "lazy": [
                {k: (v.isoformat() if isinstance(v, date) else v) for k, v in c.items()}
                for c in self.lazy
            ],
            "lazy_kop": self.lazy_total,
            "debts": [
                {
                    "entry_id": e.id,
                    "kind": e.kind.value,
                    "title": KIND_TITLES[e.kind],
                    "reason": e.reason,
                    "date": timeutil.msk_date(e.occurred_at).isoformat(),
                    "outstanding_kop": left,
                    "deducted_kop": next((a for i, a in self.allocations if i == e.id), 0),
                }
                for e, left in self.debts
            ],
            "deduction_kop": self.deduction,
            "debt_left_kop": self.debt_left,
            "total_kop": self.total,
            "credit_entry_ids": [e.id for e in self.credits],
        }


async def calculate(
    session: AsyncSession, user_id: int, period_from: date, period_to: date
) -> PayoutCalc:
    """Расчёт выплаты (ТЗ 11.3, 11.5, 11.6). Ничего не записывает."""
    if period_to < period_from:
        raise AppError("bad_period", "Конец периода раньше начала")
    lo, hi = timeutil.msk_day_bounds(period_from, period_to)
    calc = PayoutCalc(user_id, period_from, period_to)
    calc.credits = list(
        (
            await session.execute(
                select(LedgerEntry)
                .where(
                    LedgerEntry.user_id == user_id,
                    LedgerEntry.payout_id.is_(None),
                    LedgerEntry.amount_kop > 0,
                    LedgerEntry.occurred_at >= lo,
                    LedgerEntry.occurred_at < hi,
                )
                .order_by(LedgerEntry.occurred_at, LedgerEntry.id)
            )
        ).scalars()
    )
    calc.gross = sum(e.amount_kop for e in calc.credits)

    # Сниженная ставка: накопленные единицы списываются первыми, по самым ранним начислениям.
    balance: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in await lazy_balance(session, user_id):
        if row["left"] > 0:
            balance[row["product_id"]].append(row)
    for e in calc.credits:
        if e.kind != EntryKind.EARNING or not e.units:
            continue
        units_left = e.units
        for row in balance.get(e.product_id, []):
            if units_left == 0:
                break
            take = min(units_left, row["left"])
            if take <= 0:
                continue
            row["left"] -= take
            units_left -= take
            diff = max(0, (e.rate_kop or 0) - row["reduced_rate_kop"])
            calc.lazy.append(
                {
                    "lazy_id": row["id"],
                    "product_id": e.product_id,
                    "day": row["day"],
                    "units": take,
                    "normal_rate_kop": e.rate_kop,
                    "reduced_rate_kop": row["reduced_rate_kop"],
                    "minus_kop": take * diff,
                }
            )
    calc.lazy_total = sum(c["minus_kop"] for c in calc.lazy)

    # Долги: непогашенные отрицательные записи, старые первыми (ТЗ 11.3).
    negatives = (
        (
            await session.execute(
                select(LedgerEntry)
                .where(
                    LedgerEntry.user_id == user_id,
                    LedgerEntry.payout_id.is_(None),
                    LedgerEntry.amount_kop < 0,
                    LedgerEntry.occurred_at < hi,
                )
                .order_by(LedgerEntry.occurred_at, LedgerEntry.id)
            )
        )
        .scalars()
        .all()
    )
    allocated = dict(
        (
            await session.execute(
                select(PayoutAllocation.entry_id, func.sum(PayoutAllocation.amount_kop))
                .where(PayoutAllocation.entry_id.in_([e.id for e in negatives] or [0]))
                .group_by(PayoutAllocation.entry_id)
            )
        ).all()
    )
    calc.debts = [(e, -e.amount_kop - int(allocated.get(e.id, 0))) for e in negatives]
    calc.debts = [(e, left) for e, left in calc.debts if left > 0]

    available = calc.gross - calc.lazy_total
    for e, left in calc.debts:
        if available <= 0:
            break
        part = min(left, available)
        calc.allocations.append((e.id, part))
        available -= part
        calc.deduction += part
    calc.total = calc.gross - calc.lazy_total - calc.deduction
    calc.debt_left = sum(left for _, left in calc.debts) - calc.deduction
    assert calc.total >= 0
    return calc


async def preview(
    session: AsyncSession, actor: Actor, user_id: int, period_from: date, period_to: date
) -> dict[str, Any]:
    await get_target(session, actor, Perm.PAYOUTS_MANAGE, user_id)
    calc = await calculate(session, user_id, period_from, period_to)
    names = dict((await session.execute(select(Product.id, Product.name))).all())
    return calc.snapshot(names)


async def confirm(
    session: AsyncSession,
    actor: Actor,
    user_id: int,
    period_from: date,
    period_to: date,
    *,
    idempotency_key: str,
    expected_total_kop: int | None,
    comment: str | None,
) -> Payout:
    """Подтвердить выплату: неизменяемый снимок, привязка начислений, погашение долгов."""
    await get_target(session, actor, Perm.PAYOUTS_MANAGE, user_id)
    key = f"{actor.id}:{idempotency_key}"[:128]
    if (
        done := await session.scalar(select(Payout).where(Payout.idempotency_key == key))
    ) is not None:
        return done
    # Блокировка сотрудника: две выплаты одному человеку не считаются параллельно.
    await session.get(User, user_id, with_for_update=True)
    calc = await calculate(session, user_id, period_from, period_to)
    if expected_total_kop is not None and expected_total_kop != calc.total:
        raise Conflict("changed", "Данные изменились после предпросмотра — обновите расчёт")
    if not calc.credits and not calc.allocations:
        raise Conflict("nothing", "За этот период нечего выплачивать")
    names = dict((await session.execute(select(Product.id, Product.name))).all())
    snapshot = calc.snapshot(names)
    payout = Payout(
        user_id=user_id,
        period_from=period_from,
        period_to=period_to,
        amount_kop=calc.total,
        snapshot=snapshot,
        author_id=actor.id,
        comment=(comment or "").strip() or None,
        idempotency_key=key,
    )
    session.add(payout)
    await session.flush()

    for e in calc.credits:
        e.payout_id = payout.id
    for entry_id, amount in calc.allocations:
        session.add(PayoutAllocation(payout_id=payout.id, entry_id=entry_id, amount_kop=amount))
    for c in calc.lazy:
        session.add(
            LazyConsumption(
                payout_id=payout.id,
                lazy_id=c["lazy_id"],
                units=c["units"],
                normal_rate_kop=c["normal_rate_kop"],
                reduced_rate_kop=c["reduced_rate_kop"],
            )
        )
    if calc.lazy_total:
        session.add(
            LedgerEntry(
                user_id=user_id,
                kind=EntryKind.LAZY_ADJUSTMENT,
                amount_kop=-calc.lazy_total,
                units=sum(c["units"] for c in calc.lazy),
                occurred_at=timeutil.now_utc(),
                author_id=actor.id,
                reason="Оплата накопленных единиц по сниженной ставке",
                payout_id=payout.id,
                dedup_key=f"lazy:{payout.id}",
            )
        )

    # Задания, вошедшие в выплату, становятся «Оплачено».
    task_ids = [e.task_id for e in calc.credits if e.kind == EntryKind.EARNING and e.task_id]
    if task_ids:
        for t in (
            await session.execute(select(Task).where(Task.id.in_(task_ids)).with_for_update())
        ).scalars():
            if t.status == TaskStatus.APPROVED:
                t.status = TaskStatus.PAID
                add_event(session, t, "paid", actor.id, payout_id=payout.id)

    audit.record(
        session,
        category=Category.FINANCE,
        action="payout.confirmed",
        actor_id=actor.id,
        target_user_id=user_id,
        object_type="payout",
        object_id=payout.id,
        new={"amount_kop": calc.total, "period": [period_from.isoformat(), period_to.isoformat()]},
    )
    await _notify(
        session,
        user_id,
        f"payout:{payout.id}",
        f"Выплата за {period_from:%d.%m}–{period_to:%d.%m}: {rub(calc.total)}"
        + (f"\nУдержано: {rub(calc.deduction)}" if calc.deduction else ""),
    )
    await session.commit()
    return payout


# --- сводка для экранов -------------------------------------------------------------


async def overview(
    session: AsyncSession, user_id: int, start: date | None, end: date | None
) -> dict[str, Any]:
    q = select(LedgerEntry).where(LedgerEntry.user_id == user_id)
    if start and end:
        lo, hi = timeutil.msk_day_bounds(start, end)
        q = q.where(LedgerEntry.occurred_at >= lo, LedgerEntry.occurred_at < hi)
    entries = (
        (
            await session.execute(
                q.order_by(LedgerEntry.occurred_at.desc(), LedgerEntry.id.desc()).limit(500)
            )
        )
        .scalars()
        .all()
    )
    names = dict((await session.execute(select(Product.id, Product.name))).all())
    # Непогашенный остаток долгов и невыплаченное — по всем записям, без фильтра периода.
    unpaid = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_kop), 0)).where(
            LedgerEntry.user_id == user_id,
            LedgerEntry.payout_id.is_(None),
            LedgerEntry.amount_kop > 0,
        )
    )
    neg = await session.scalar(
        select(func.coalesce(func.sum(-LedgerEntry.amount_kop), 0)).where(
            LedgerEntry.user_id == user_id,
            LedgerEntry.payout_id.is_(None),
            LedgerEntry.amount_kop < 0,
        )
    )
    allocated = await session.scalar(
        select(func.coalesce(func.sum(PayoutAllocation.amount_kop), 0))
        .join(LedgerEntry, LedgerEntry.id == PayoutAllocation.entry_id)
        .where(LedgerEntry.user_id == user_id)
    )
    reversed_bonus = set(
        (
            await session.execute(
                select(LedgerEntry.source_entry_id).where(
                    LedgerEntry.user_id == user_id, LedgerEntry.kind == EntryKind.BONUS_REVERSAL
                )
            )
        ).scalars()
    )
    payouts = (
        (
            await session.execute(
                select(Payout).where(Payout.user_id == user_id).order_by(Payout.id.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    lazy = await lazy_balance(session, user_id)
    return {
        "unpaid_kop": int(unpaid),
        "debt_kop": int(neg) - int(allocated),
        "entries": [
            {
                "id": e.id,
                "kind": e.kind.value,
                "title": KIND_TITLES[e.kind],
                "product": names.get(e.product_id),
                "task_id": e.task_id,
                "units": e.units,
                "rate_kop": e.rate_kop,
                "amount_kop": e.amount_kop,
                "occurred_at": e.occurred_at,
                "reason": e.reason,
                "paid": e.payout_id is not None,
                "cancelled": e.id in reversed_bonus,
                "source_entry_id": e.source_entry_id,
            }
            for e in entries
        ],
        "payouts": [
            {
                "id": p.id,
                "period_from": p.period_from,
                "period_to": p.period_to,
                "amount_kop": p.amount_kop,
                "created_at": p.created_at,
                "snapshot": p.snapshot,
            }
            for p in payouts
        ],
        "lazy": {
            "accrued": sum(x["units"] for x in lazy),
            "used": sum(x["used"] for x in lazy),
            "left": sum(x["left"] for x in lazy),
            "items": [{**x, "product": names.get(x["product_id"])} for x in lazy[-30:]],
        },
    }


async def unpaid_list(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    actor.require(Perm.PAYOUTS_MANAGE)
    scope = await scope_for(session, actor, Perm.PAYOUTS_MANAGE)
    credit = func.coalesce(func.sum(LedgerEntry.amount_kop).filter(LedgerEntry.amount_kop > 0), 0)
    debit = func.coalesce(func.sum(-LedgerEntry.amount_kop).filter(LedgerEntry.amount_kop < 0), 0)
    q = (
        select(LedgerEntry.user_id, credit, debit)
        .where(LedgerEntry.payout_id.is_(None))
        .group_by(LedgerEntry.user_id)
    )
    if scope is not ALL:
        q = q.where(LedgerEntry.user_id.in_(scope))
    rows = (await session.execute(q)).all()
    allocated = dict(
        (
            await session.execute(
                select(LedgerEntry.user_id, func.sum(PayoutAllocation.amount_kop))
                .join(LedgerEntry, LedgerEntry.id == PayoutAllocation.entry_id)
                .group_by(LedgerEntry.user_id)
            )
        ).all()
    )
    users = {
        u.id: u
        for u in (
            await session.execute(select(User).where(User.id.in_([r[0] for r in rows] or [0])))
        ).scalars()
    }
    out = []
    for uid, cr, db in rows:
        debt = int(db) - int(allocated.get(uid, 0))
        if cr or debt:
            out.append(
                {
                    "user_id": uid,
                    "full_name": users[uid].full_name,
                    "unpaid_kop": int(cr),
                    "debt_kop": debt,
                }
            )
    return sorted(out, key=lambda r: r["full_name"])
