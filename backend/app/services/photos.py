"""Загрузка фото и проверка OCR (ТЗ 7)."""

import asyncio
import logging
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ocr, storage
from app.errors import Conflict, NotFound
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
from app.services import notify, photoreports
from app.services.access import Actor, in_scope, scope_for
from app.services.execution import own_task, require_active, require_found
from app.services.tasks import add_event

log = logging.getLogger(__name__)


async def upload(
    session: AsyncSession,
    actor: Actor,
    task_id: int,
    *,
    kind: PhotoKind,
    stage: int | None,
    data: bytes,
) -> tuple[TaskPhoto, Task]:
    task = await own_task(session, actor, task_id)
    require_active(task)
    require_found(task)
    product = await session.get(Product, task.product_id)
    flow = product.flow
    if kind == PhotoKind.STAGE:
        if stage is None or stage != task.current_stage or stage >= len(flow):
            raise Conflict("wrong_stage", "Фото можно сделать только на текущем этапе")
        if flow[stage].get("photo", "none") == "none":
            raise Conflict("photo_not_needed", "На этом этапе фото не требуется")
    else:
        stage = None
        if task.current_stage < len(flow):
            raise Conflict("stages_left", "Сначала пройдите все этапы")
        if product.ocr_enabled and task.ocr_attempts_used >= product.ocr_attempts:
            raise Conflict("ocr_exhausted", "Попытки проверки исчерпаны")

    # Файл пишется на диск до записи в БД: строка появляется только для сохранённого файла.
    rel, ctype, digest = await asyncio.to_thread(storage.save_photo, data)
    photo = TaskPhoto(
        task_id=task.id,
        user_id=actor.id,
        product_id=product.id,
        kind=kind,
        stage=stage,
        path=rel,
        content_type=ctype,
        size_bytes=len(data),
        sha256=digest,
    )
    session.add(photo)
    await session.flush()
    add_event(
        session,
        task,
        "photo_uploaded",
        actor.id,
        stage=stage,
        photo_id=photo.id,
        photo_kind=kind.value,
    )
    await session.commit()
    # Отправка в Telegram — отдельной транзакцией: сбой маршрутизации не влияет на сохранение фото.
    try:
        await photoreports.enqueue_photo(session, photo)
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception("photo report enqueue failed for photo %s", photo.id)

    if kind == PhotoKind.FINAL and product.ocr_enabled:
        await _check_ocr(session, actor, task.id, photo.id)
        await session.refresh(photo)
        await session.refresh(task)
    return photo, task


async def _check_ocr(session: AsyncSession, actor: Actor, task_id: int, photo_id: int) -> None:
    """OCR выполняется вне транзакции; результат записывается под блокировкой задания."""
    photo = await session.get(TaskPhoto, photo_id)
    task = await session.get(Task, task_id)
    product = await session.get(Product, task.product_id)
    expected = task.expected_text or product.expected_text_default

    status: OcrStatus
    text: str | None = None
    detail: str | None = None
    to_manual: str | None = None
    if not expected:
        status, detail = OcrStatus.ERROR, "Не задан ожидаемый текст"
        to_manual = "Не задан ожидаемый текст для проверки фото"
    else:
        try:
            text = await ocr.get_engine().recognize(storage.photo_path(photo.path))
        except ocr.OcrUnavailable as e:
            status, detail = OcrStatus.ERROR, str(e)
            to_manual = "Автоматическая проверка недоступна"
        except Exception as e:  # noqa: BLE001 — битое изображение, таймаут и т. п.
            log.exception("OCR failed for photo %s", photo_id)
            status, detail = OcrStatus.FAILED, f"Не удалось распознать фото: {type(e).__name__}"
        else:
            ok, score = ocr.matches(product.ocr_rule.value, expected, text, product.ocr_threshold)
            status = OcrStatus.PASSED if ok else OcrStatus.FAILED
            detail = f"Совпадение {round(score * 100)}%"

    task = await session.get(Task, task_id, with_for_update=True, populate_existing=True)
    photo.ocr_status = status
    photo.ocr_text = text
    photo.ocr_detail = detail
    if status != OcrStatus.ERROR:
        task.ocr_attempts_used += 1
        photo.ocr_attempt = task.ocr_attempts_used
    add_event(
        session,
        task,
        "ocr_attempt",
        actor.id,
        photo_id=photo.id,
        status=status.value,
        detail=detail,
        attempt=photo.ocr_attempt,
    )
    if status == OcrStatus.FAILED and task.ocr_attempts_used >= product.ocr_attempts:
        to_manual = f"Исчерпаны попытки проверки фото ({product.ocr_attempts})"
    if to_manual and task.status == TaskStatus.ACTIVE:
        await send_to_manual_review(session, task, product, to_manual, actor.id)
    await session.commit()


async def send_to_manual_review(
    session: AsyncSession, task: Task, product: Product, reason: str, actor_id: int | None
) -> None:
    task.status = TaskStatus.MANUAL_REVIEW
    task.manual_review_reason = reason
    ev = add_event(session, task, "manual_review", actor_id, reason=reason)
    await session.flush()
    user = await session.get(User, task.assignee_id)
    await notify.notify_holders(
        session,
        Perm.OCR_MANUAL_REVIEW,
        dedup_key=f"task_event:{ev.id}",
        text=(
            f"<b>Ручная проверка</b>\n{escape(product.name)}, задание {escape(task.external_id)}\n"
            f"{escape(user.full_name)}: {escape(reason)}"
        ),
        button="Проверить",
    )


async def photo_for_viewer(session: AsyncSession, actor: Actor, photo_id: int) -> TaskPhoto:
    """Фото видит автор, а также проверяющие в пределах своей области (ТЗ 19)."""
    photo = await session.get(TaskPhoto, photo_id)
    if photo is None:
        raise NotFound("Фото не найдено")
    if photo.user_id == actor.id or actor.is_owner:
        return photo
    for perm in (Perm.MODERATION_REVIEW, Perm.OCR_MANUAL_REVIEW, Perm.TASKS_MANAGE):
        if actor.has(perm) and in_scope(await scope_for(session, actor, perm), photo.user_id):
            return photo
    raise NotFound("Фото не найдено")


async def task_photos(session: AsyncSession, task_id: int) -> list[TaskPhoto]:
    rows = await session.execute(
        select(TaskPhoto).where(TaskPhoto.task_id == task_id).order_by(TaskPhoto.id)
    )
    return list(rows.scalars())
