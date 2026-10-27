"""Финансовый журнал: начисления, сторно, штрафы, бонусы, выплаты (ТЗ 11)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EntryKind, LedgerEntry, Product, Task, TaskStatus, User
from app.services import products as products_svc

APPROVED = (TaskStatus.APPROVED, TaskStatus.PAID)


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
