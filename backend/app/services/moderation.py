"""Модерация, ручная проверка фото и расчёт качества (ТЗ 7.2, 8)."""

from dataclasses import dataclass
from datetime import datetime
from html import escape

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import Product, Task, TaskStatus, User
from app.permissions import Perm
from app.services import audit, outbox
from app.services.access import Actor, in_scope, scope_for
from app.services.audit import Category
from app.services.tasks import add_event
from app.timeutil import now_utc

APPROVED_STATUSES = (TaskStatus.APPROVED, TaskStatus.PAID)


async def _task_in_scope(session: AsyncSession, actor: Actor, perm: Perm, task_id: int) -> Task:
    actor.require(perm)
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None or task.assignee_id is None:
        raise NotFound("Задание не найдено")
    if not in_scope(await scope_for(session, actor, perm), task.assignee_id):
        raise NotFound("Задание не найдено")
    if task.assignee_id == actor.id and not actor.is_owner:
        raise Forbidden("own_task", "Нельзя проверять собственное задание")
    return task


async def _notify_employee(
    session: AsyncSession, task: Task, product: Product, key: str, text: str
) -> None:
    user = await session.get(User, task.assignee_id)
    await outbox.enqueue(
        session,
        dedup_key=key,
        chat_id=user.telegram_id,
        related_user_id=user.id,
        text=f"{escape(product.name)}, задание {escape(task.external_id)}: {text}",
        open_app_button="Открыть",
    )


# --- модерация ----------------------------------------------------------------------


async def decide(
    session: AsyncSession, actor: Actor, task_id: int, decision: str, reason: str | None
) -> Task:
    """decision: approve | reject | return.

    Решение можно изменить позже (одобренное отклонить и наоборот); финансовые последствия
    оформляются корректирующими записями в журнале (этап 7), история не переписывается.
    """
    task = await _task_in_scope(session, actor, Perm.MODERATION_REVIEW, task_id)
    product = await session.get(Product, task.product_id)
    reason = (reason or "").strip() or None
    old = task.status

    if decision == "approve":
        if old not in (TaskStatus.MODERATION, TaskStatus.REJECTED):
            raise Conflict(
                "bad_state", "Допустить к оплате можно задание на модерации или отклонённое"
            )
        new = TaskStatus.APPROVED
    elif decision == "reject":
        if old not in (TaskStatus.MODERATION, TaskStatus.APPROVED, TaskStatus.PAID):
            raise Conflict("bad_state", "Это задание нельзя отклонить")
        if not reason:
            raise AppError("reason_required", "Укажите причину отклонения")
        new = TaskStatus.REJECTED
    elif decision == "return":
        if old != TaskStatus.MODERATION:
            raise Conflict("bad_state", "Вернуть на исправление можно только задание на модерации")
        if not product.allow_return_for_fix:
            raise Conflict("not_allowed", "Для этого продукта возврат на исправление отключён")
        if not reason:
            raise AppError("reason_required", "Укажите, что нужно исправить")
        new = TaskStatus.ACTIVE
    else:
        raise AppError("bad_decision", "Неизвестное решение")

    now = now_utc()
    task.status = new
    task.moderated_by_id = actor.id
    task.moderated_at = now
    task.moderation_reason = reason
    if new == TaskStatus.ACTIVE:
        # Сотрудник переделывает финальную часть: новое фото и новые попытки проверки.
        task.completed_at = None
        task.ocr_attempts_used = 0
    ev = add_event(
        session, task, "moderation", actor.id, decision=decision, reason=reason, previous=old.value
    )
    audit.record(
        session,
        category=Category.TASKS,
        action=f"moderation.{decision}",
        actor_id=actor.id,
        target_user_id=task.assignee_id,
        object_type="task",
        object_id=task.id,
        old={"status": old},
        new={"status": new},
        reason=reason,
    )
    await session.flush()

    from app.services import finance  # noqa: PLC0415 — финансы зависят от модерации

    await finance.on_moderation_changed(session, task, product, old, new, actor.id)

    if decision == "reject":
        await _notify_employee(
            session, task, product, f"task_event:{ev.id}", f"отклонено.\nПричина: {escape(reason)}"
        )
    elif decision == "return":
        await _notify_employee(
            session,
            task,
            product,
            f"task_event:{ev.id}",
            f"возвращено на исправление.\n{escape(reason)}",
        )
    await session.commit()
    return task


# --- ручная проверка ----------------------------------------------------------------


async def manual_approve(
    session: AsyncSession, actor: Actor, task_id: int, reason: str | None
) -> Task:
    task = await _task_in_scope(session, actor, Perm.OCR_MANUAL_REVIEW, task_id)
    if task.status != TaskStatus.MANUAL_REVIEW:
        raise Conflict("bad_state", "Задание не ожидает ручной проверки")
    product = await session.get(Product, task.product_id)
    reason = (reason or "").strip() or None
    stages_done = task.found_at is not None and task.current_stage >= len(product.flow)
    if stages_done:
        # Проверка пройдена вручную — задание идёт на модерацию как завершённое.
        task.status = TaskStatus.MODERATION
        task.completed_at = task.completed_at or now_utc()
    else:
        task.status = TaskStatus.ACTIVE
    task.manual_review_reason = None
    ev = add_event(session, task, "manual_approved", actor.id, reason=reason, to=task.status.value)
    audit.record(
        session,
        category=Category.PHOTOS,
        action="manual_review.approved",
        actor_id=actor.id,
        target_user_id=task.assignee_id,
        object_type="task",
        object_id=task.id,
        old={"status": TaskStatus.MANUAL_REVIEW},
        new={"status": task.status},
        reason=reason,
    )
    await session.flush()
    if task.status == TaskStatus.ACTIVE:
        await _notify_employee(
            session, task, product, f"task_event:{ev.id}", "можно продолжать выполнение"
        )
    await session.commit()
    return task


async def manual_return(
    session: AsyncSession, actor: Actor, task_id: int, reason: str | None
) -> Task:
    task = await _task_in_scope(session, actor, Perm.OCR_MANUAL_REVIEW, task_id)
    if task.status != TaskStatus.MANUAL_REVIEW:
        raise Conflict("bad_state", "Задание не ожидает ручной проверки")
    reason = (reason or "").strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину возврата")
    product = await session.get(Product, task.product_id)
    task.status = TaskStatus.ACTIVE
    task.manual_review_reason = None
    task.ocr_attempts_used = 0  # новые попытки проверки
    ev = add_event(session, task, "manual_returned", actor.id, reason=reason)
    audit.record(
        session,
        category=Category.PHOTOS,
        action="manual_review.returned",
        actor_id=actor.id,
        target_user_id=task.assignee_id,
        object_type="task",
        object_id=task.id,
        old={"status": TaskStatus.MANUAL_REVIEW},
        new={"status": TaskStatus.ACTIVE},
        reason=reason,
    )
    await session.flush()
    await _notify_employee(
        session, task, product, f"task_event:{ev.id}", f"возвращено вам.\n{escape(reason)}"
    )
    await session.commit()
    return task


async def manual_cancel(
    session: AsyncSession, actor: Actor, task_id: int, reason: str | None
) -> Task:
    task = await _task_in_scope(session, actor, Perm.OCR_MANUAL_REVIEW, task_id)
    if task.status != TaskStatus.MANUAL_REVIEW:
        raise Conflict("bad_state", "Задание не ожидает ручной проверки")
    reason = (reason or "").strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину отмены")
    task.status = TaskStatus.CANCELLED
    add_event(session, task, "cancelled", actor.id, reason=reason)
    audit.record(
        session,
        category=Category.TASKS,
        action="task.cancelled",
        actor_id=actor.id,
        target_user_id=task.assignee_id,
        object_type="task",
        object_id=task.id,
        old={"status": TaskStatus.MANUAL_REVIEW},
        new={"status": TaskStatus.CANCELLED},
        reason=reason,
    )
    await session.commit()
    return task


# --- качество -----------------------------------------------------------------------


@dataclass
class Quality:
    approved_units: int
    rejected_units: int
    pending_units: int
    paid_units: int

    @property
    def reviewed_units(self) -> int:
        return self.approved_units + self.rejected_units

    @property
    def percent(self) -> float | None:
        """Качество = одобренные / (одобренные + отклонённые) × 100%. Без решений — None («—»)."""
        if self.reviewed_units == 0:
            return None
        return self.approved_units * 100 / self.reviewed_units

    def as_dict(self, min_sample: int) -> dict:
        return {
            "approved_units": self.approved_units,
            "rejected_units": self.rejected_units,
            "pending_units": self.pending_units,
            "paid_units": self.paid_units,
            "reviewed_units": self.reviewed_units,
            "percent": None if self.percent is None else round(self.percent, 1),
            "small_sample": self.reviewed_units < min_sample,
        }


async def quality(
    session: AsyncSession,
    *,
    user_ids: list[int] | None = None,
    product_ids: list[int] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Quality:
    """Единицы по статусам за период (по времени завершения задания)."""

    def units_where(*statuses):
        return func.coalesce(func.sum(case((Task.status.in_(statuses), Task.units), else_=0)), 0)

    q = select(
        units_where(TaskStatus.APPROVED, TaskStatus.PAID),
        units_where(TaskStatus.REJECTED),
        units_where(TaskStatus.MODERATION),
        units_where(TaskStatus.PAID),
    ).where(Task.completed_at.is_not(None))
    if user_ids is not None:
        q = q.where(Task.assignee_id.in_(user_ids))
    if product_ids is not None:
        q = q.where(Task.product_id.in_(product_ids))
    if since is not None:
        q = q.where(Task.completed_at >= since)
    if until is not None:
        q = q.where(Task.completed_at < until)
    a, r, p, paid = (await session.execute(q)).one()
    return Quality(int(a), int(r), int(p), int(paid))
