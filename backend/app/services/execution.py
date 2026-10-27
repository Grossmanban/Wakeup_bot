"""Выполнение задания сотрудником (ТЗ 6). Всё время — серверное."""

from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, Conflict, NotFound
from app.models import (
    OcrStatus,
    PhotoKind,
    Product,
    Task,
    TaskPhoto,
    TaskStatus,
    User,
)
from app.permissions import Perm
from app.services import notify
from app.services.access import Actor
from app.services.tasks import add_event
from app.timeutil import now_utc

OTHER_REASON = "Другое"


async def own_task(session: AsyncSession, actor: Actor, task_id: int, *, lock: bool = True) -> Task:
    task = await session.get(Task, task_id, with_for_update=lock)
    if task is None or task.assignee_id != actor.id:
        raise NotFound("Задание не найдено")
    return task


def require_active(task: Task) -> None:
    if task.status == TaskStatus.MANUAL_REVIEW:
        raise Conflict("manual_review", "Задание ожидает решения администрации")
    if task.status != TaskStatus.ACTIVE:
        raise Conflict("not_active", "Задание уже не активно")


def require_found(task: Task) -> None:
    if task.found_at is None:
        raise Conflict("not_found_yet", "Сначала нажмите «Найдено»")


def stage_state(task: Task, product: Product) -> dict[str, Any]:
    """Сколько осталось ждать на текущем этапе — считается по серверному времени."""
    flow = product.flow
    n = task.current_stage
    if task.found_at is None or n >= len(flow):
        return {"stage": n, "total": len(flow), "seconds_left": 0}
    started = task.stage_started_at or task.found_at
    passed = (now_utc() - started).total_seconds()
    left = max(0, int(flow[n].get("min_seconds", 0) - passed + 0.999))
    return {"stage": n, "total": len(flow), "seconds_left": left}


async def open_link(session: AsyncSession, actor: Actor, task_id: int) -> Task:
    task = await own_task(session, actor, task_id)
    require_active(task)
    if task.opened_at is None:
        task.opened_at = now_utc()
        add_event(session, task, "opened", actor.id)
    await session.commit()
    return task


async def found(session: AsyncSession, actor: Actor, task_id: int) -> Task:
    task = await own_task(session, actor, task_id)
    require_active(task)
    if task.found_at is not None:
        return task  # повторное нажатие не сбрасывает таймер
    if task.link and task.opened_at is None:
        raise Conflict("open_first", "Сначала откройте ссылку")
    now = now_utc()
    task.found_at = now
    task.current_stage = 0
    task.stage_started_at = now
    add_event(session, task, "found", actor.id)
    await session.commit()
    return task


async def _photos(session: AsyncSession, task: Task, kind: PhotoKind, stage: int | None):
    q = select(TaskPhoto).where(TaskPhoto.task_id == task.id, TaskPhoto.kind == kind)
    if stage is not None:
        q = q.where(TaskPhoto.stage == stage)
    return list((await session.execute(q.order_by(TaskPhoto.id))).scalars())


async def complete_stage(
    session: AsyncSession, actor: Actor, task_id: int, stage: int, checklist: list[bool]
) -> Task:
    task = await own_task(session, actor, task_id)
    require_active(task)
    require_found(task)
    product = await session.get(Product, task.product_id)
    flow = product.flow
    if stage < task.current_stage:
        return task  # этап уже пройден — повтор запроса ничего не меняет
    if stage != task.current_stage or stage >= len(flow):
        raise Conflict("wrong_stage", "Этот этап сейчас недоступен")
    spec = flow[stage]

    items = spec.get("checklist", [])
    if len(checklist) != len(items) or not all(checklist):
        raise AppError("checklist", "Отметьте все пункты чек-листа")
    left = stage_state(task, product)["seconds_left"]
    if left > 0:
        raise Conflict("timer", f"Этап можно завершить через {left} с")
    if spec.get("photo") == "required" and not await _photos(session, task, PhotoKind.STAGE, stage):
        raise Conflict("photo_required", "Сделайте фото для этого этапа")

    now = now_utc()
    elapsed = int((now - (task.stage_started_at or task.found_at)).total_seconds())
    add_event(session, task, "stage_done", actor.id, stage=stage, seconds=elapsed)
    task.current_stage = stage + 1
    task.stage_started_at = now
    await session.commit()
    return task


async def reset_timer(
    session: AsyncSession, actor: Actor, task_id: int, reason: str | None
) -> Task:
    task = await own_task(session, actor, task_id)
    require_active(task)
    require_found(task)
    product = await session.get(Product, task.product_id)
    reason = (reason or "").strip() or None
    if product.timer_reset_reason_required and not reason:
        raise AppError("reason_required", "Укажите причину сброса таймера")
    old = task.stage_started_at
    task.stage_started_at = now_utc()
    add_event(
        session,
        task,
        "timer_reset",
        actor.id,
        stage=task.current_stage,
        reason=reason,
        previous_start=old.isoformat() if old else None,
    )
    await session.commit()
    return task


async def report_error(
    session: AsyncSession, actor: Actor, task_id: int, reason: str, comment: str | None
) -> Task:
    """Сообщение об ошибке переводит задание в «ожидает ручного решения»."""
    task = await own_task(session, actor, task_id)
    require_active(task)
    product = await session.get(Product, task.product_id)
    reason = " ".join(reason.split())
    allowed = [*product.error_reasons, OTHER_REASON]
    if reason not in allowed:
        raise AppError("bad_reason", "Выберите причину из списка")
    comment = (comment or "").strip() or None
    if reason == OTHER_REASON and not comment:
        raise AppError("comment_required", "Опишите проблему")
    task.status = TaskStatus.MANUAL_REVIEW
    task.manual_review_reason = f"Сообщение об ошибке: {reason}" + (
        f". {comment}" if comment else ""
    )
    ev = add_event(session, task, "error_reported", actor.id, reason=reason, comment=comment)
    await session.flush()
    user = await session.get(User, actor.id)
    await notify.notify_holders(
        session,
        Perm.OCR_MANUAL_REVIEW,
        dedup_key=f"task_event:{ev.id}",
        text=(
            "<b>Сообщение об ошибке</b>\n"
            f"{escape(product.name)}, задание {escape(task.external_id)}\n"
            f"{escape(user.full_name)}: {escape(reason)}"
            + (f"\n{escape(comment)}" if comment else "")
        ),
        button="Открыть",
    )
    await session.commit()
    return task


async def complete(session: AsyncSession, actor: Actor, task_id: int) -> Task:
    task = await own_task(session, actor, task_id)
    if task.status == TaskStatus.MODERATION:
        return task  # повторное нажатие
    require_active(task)
    require_found(task)
    product = await session.get(Product, task.product_id)
    if task.current_stage < len(product.flow):
        raise Conflict("stages_left", "Пройдите все этапы задания")
    finals = await _photos(session, task, PhotoKind.FINAL, None)
    if product.final_photo_required or product.ocr_enabled:
        if not finals:
            raise Conflict("photo_required", "Сделайте финальное фото")
        if product.ocr_enabled and finals[-1].ocr_status != OcrStatus.PASSED:
            raise Conflict("ocr_not_passed", "Финальное фото не прошло проверку текста")
    now = now_utc()
    task.completed_at = now
    task.status = TaskStatus.MODERATION
    add_event(session, task, "completed", actor.id)
    await session.commit()
    return task
