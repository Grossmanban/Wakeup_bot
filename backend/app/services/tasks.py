"""Общий пул и выдача заданий (ТЗ 5)."""

from html import escape
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import (
    Product,
    ProductAccess,
    ProductNetwork,
    ProductStatus,
    Task,
    TaskEvent,
    TaskStatus,
    User,
    UserNetwork,
)
from app.permissions import Perm
from app.services import audit, idempotency, outbox
from app.services import products as products_svc
from app.services.access import TEAM_ROLES, Actor, in_scope, scope_for
from app.services.audit import Category
from app.timeutil import now_utc


def add_event(
    session: AsyncSession,
    task: Task,
    kind: str,
    user_id: int | None,
    *,
    stage: int | None = None,
    **data: Any,
) -> TaskEvent:
    ev = TaskEvent(task_id=task.id, user_id=user_id, kind=kind, stage=stage, data=data)
    session.add(ev)
    return ev


async def _check_area(session: AsyncSession, user: User, product_id: int) -> None:
    allowed = set(
        (
            await session.execute(
                select(ProductNetwork.network_id).where(ProductNetwork.product_id == product_id)
            )
        ).scalars()
    )
    if not allowed:
        return
    mine = set(
        (
            await session.execute(
                select(UserNetwork.network_id).where(UserNetwork.user_id == user.id)
            )
        ).scalars()
    )
    if not allowed & mine:
        raise Forbidden("area", "Задания этого продукта недоступны в вашей сети")


async def take_task(
    session: AsyncSession, actor: Actor, product_id: int, *, idem_key: str | None = None
) -> Task:
    """Атомарно выдать одно задание из пула.

    Строка сотрудника блокируется, чтобы параллельные запросы одного человека не превысили
    лимит; строка задания берётся с SKIP LOCKED, поэтому два человека не получат одно задание.
    """
    user = await session.get(User, actor.id, with_for_update=True)
    assert user is not None
    product = await session.get(Product, product_id)
    if product is None or product.status == ProductStatus.DELETED:
        raise NotFound("Продукт не найден")
    has_access = await session.scalar(
        select(ProductAccess.user_id).where(
            ProductAccess.user_id == user.id, ProductAccess.product_id == product.id
        )
    )
    if not has_access:
        raise Forbidden("no_product_access", "У вас нет доступа к этому продукту")
    if product.status != ProductStatus.ACTIVE:
        raise Conflict("product_inactive", "Продукт сейчас недоступен для новых заданий")
    await _check_area(session, user, product.id)
    # Без настроенной ставки работу не начинаем: ноль молча не подставляется.
    await products_svc.resolve_rate(session, user, product.id)

    active = await session.scalar(
        select(func.count()).where(
            Task.assignee_id == user.id,
            Task.product_id == product.id,
            Task.status == TaskStatus.ACTIVE,
        )
    )
    if active >= product.active_task_limit:
        raise Conflict(
            "task_limit",
            f"Достигнут лимит активных заданий ({product.active_task_limit}). "
            "Завершите текущее задание",
        )

    gender_ok = or_(Task.gender.is_(None), Task.gender == user.gender)
    if product.gender and product.gender != user.gender:
        raise Forbidden("gender", "Задания этого продукта вам недоступны")
    task = await session.scalar(
        select(Task)
        .where(Task.product_id == product.id, Task.status == TaskStatus.POOL, gender_ok)
        .order_by(Task.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if task is None:
        raise Conflict("pool_empty", "Свободных заданий сейчас нет")

    now = now_utc()
    task.status = TaskStatus.ACTIVE
    task.assignee_id = user.id
    task.assigned_at = now
    add_event(session, task, "assigned", user.id)
    await idempotency.remember(
        session, user.id, idem_key, f"take:{product_id}", {"task_id": task.id}
    )
    await session.commit()
    return task


async def get_task_for_manager(session: AsyncSession, actor: Actor, task_id: int) -> Task:
    actor.require(Perm.TASKS_MANAGE)
    # Блокировка до проверки области: исполнитель не сменится между проверкой и действием.
    task = await session.get(Task, task_id, with_for_update=True)
    if task is None:
        raise NotFound("Задание не найдено")
    if actor.is_owner:
        return task
    if task.assignee_id is not None:
        scope = await scope_for(session, actor, Perm.TASKS_MANAGE)
        if not in_scope(scope, task.assignee_id):
            raise NotFound("Задание не найдено")
    elif not await _manages_pool(session, actor, task.product_id):
        raise NotFound("Задание не найдено")
    return task


async def _manages_pool(session: AsyncSession, actor: Actor, product_id: int) -> bool:
    """Задания в пуле ничьи: ими управляет право на все сети или на сеть продукта."""
    if actor.user.role in TEAM_ROLES:
        return False
    if actor.has_global(Perm.TASKS_MANAGE):
        return True
    granted = {n for n in actor.grants.get(Perm.TASKS_MANAGE.value, set()) if n is not None}
    networks = set(
        (
            await session.execute(
                select(ProductNetwork.network_id).where(ProductNetwork.product_id == product_id)
            )
        ).scalars()
    )
    return bool(networks & granted)


def reset_execution(task: Task) -> None:
    task.assignee_id = None
    task.assigned_at = None
    task.opened_at = None
    task.found_at = None
    task.current_stage = 0
    task.stage_started_at = None
    task.completed_at = None
    task.ocr_attempts_used = 0
    task.manual_review_reason = None
    task.attempt += 1


async def return_to_pool(
    session: AsyncSession, actor: Actor, task_id: int, reason: str | None
) -> Task:
    task = await get_task_for_manager(session, actor, task_id)
    await session.refresh(task, with_for_update=True)
    if task.status not in (TaskStatus.ACTIVE, TaskStatus.MANUAL_REVIEW):
        raise Conflict(
            "bad_state", "Вернуть в пул можно только активное задание или задание на ручном решении"
        )
    reason = (reason or "").strip() or None
    prev_user = task.assignee_id
    prev_status = task.status
    reset_execution(task)
    task.status = TaskStatus.POOL
    add_event(session, task, "returned_to_pool", actor.id, reason=reason, from_user=prev_user)
    ev = audit.record(
        session,
        category=Category.TASKS,
        action="task.returned_to_pool",
        actor_id=actor.id,
        target_user_id=prev_user,
        object_type="task",
        object_id=task.id,
        old={"status": prev_status, "assignee_id": prev_user},
        new={"status": TaskStatus.POOL},
        reason=reason,
    )
    await session.flush()
    if prev_user:
        user = await session.get(User, prev_user)
        product = await session.get(Product, task.product_id)
        await outbox.enqueue(
            session,
            dedup_key=f"audit:{ev.id}",
            chat_id=user.telegram_id,
            related_user_id=user.id,
            text=(
                f"Задание {escape(task.external_id)} ({escape(product.name)}) "
                "возвращено в общий пул" + (f".\nПричина: {escape(reason)}" if reason else "")
            ),
        )
    await session.commit()
    return task


async def cancel_task(
    session: AsyncSession, actor: Actor, task_id: int, reason: str | None
) -> Task:
    """Отменить задание с сохранением истории (ТЗ 7.2)."""
    task = await get_task_for_manager(session, actor, task_id)
    await session.refresh(task, with_for_update=True)
    if task.status in (TaskStatus.PAID, TaskStatus.CANCELLED, TaskStatus.ARCHIVED):
        raise Conflict("bad_state", "Это задание нельзя отменить")
    if task.status in (TaskStatus.APPROVED,):
        raise Conflict("bad_state", "Задание уже допущено к оплате — измените решение модерации")
    reason = (reason or "").strip()
    if not reason:
        raise AppError("reason_required", "Укажите причину отмены")
    old = task.status
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
        old={"status": old},
        new={"status": TaskStatus.CANCELLED},
        reason=reason,
    )
    await session.commit()
    return task


async def pool_counts(session: AsyncSession, product_ids: set[int], user: User) -> dict[int, int]:
    if not product_ids:
        return {}
    rows = await session.execute(
        select(Task.product_id, func.count())
        .where(
            Task.product_id.in_(product_ids),
            Task.status == TaskStatus.POOL,
            or_(Task.gender.is_(None), Task.gender == user.gender),
        )
        .group_by(Task.product_id)
    )
    return dict(rows.all())
