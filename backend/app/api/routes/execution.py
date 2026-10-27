from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import storage
from app.api.deps import ActorDep, SessionDep
from app.api.schemas_products import ReasonIn, TaskOut
from app.errors import NotFound
from app.models import PhotoKind, Product, Task, TaskEvent, TaskPhoto, TaskStatus, User
from app.permissions import Perm
from app.services import execution, moderation, photos
from app.services.access import ALL, scope_for

router = APIRouter(tags=["execution"])


class PhotoOut(BaseModel):
    id: int
    kind: PhotoKind
    stage: int | None
    uploaded_at: str
    ocr_status: str | None
    ocr_text: str | None
    ocr_detail: str | None
    ocr_attempt: int | None
    attempt: int


def photo_out(p: TaskPhoto) -> PhotoOut:
    return PhotoOut(
        id=p.id,
        kind=p.kind,
        stage=p.stage,
        uploaded_at=p.uploaded_at.isoformat(),
        ocr_status=p.ocr_status.value if p.ocr_status else None,
        ocr_text=p.ocr_text,
        ocr_detail=p.ocr_detail,
        ocr_attempt=p.ocr_attempt,
        attempt=p.attempt,
    )


async def _my_task_view(session, task: Task) -> dict:
    product = await session.get(Product, task.product_id)
    pics = await photos.task_photos(session, task)
    return {
        "task": TaskOut.model_validate(task),
        "product": {
            "id": product.id,
            "name": product.name,
            "instructions": product.instructions,
            "links": product.links,
            "materials": product.materials,
            "flow": product.flow,
            "final_photo_required": product.final_photo_required,
            "timer_reset_reason_required": product.timer_reset_reason_required,
            "error_reasons": product.error_reasons,
            "ocr_enabled": product.ocr_enabled,
            "ocr_attempts": product.ocr_attempts,
        },
        "stage_state": execution.stage_state(task, product),
        "photos": [photo_out(p) for p in pics],
        "ocr_attempts_left": max(0, product.ocr_attempts - task.ocr_attempts_used),
    }


@router.get("/my/tasks/{task_id}")
async def my_task(task_id: int, session: SessionDep, actor: ActorDep):
    task = await execution.own_task(session, actor, task_id, lock=False)
    return await _my_task_view(session, task)


@router.post("/my/tasks/{task_id}/open")
async def open_link(task_id: int, session: SessionDep, actor: ActorDep):
    return await _my_task_view(session, await execution.open_link(session, actor, task_id))


@router.post("/my/tasks/{task_id}/found")
async def found(task_id: int, session: SessionDep, actor: ActorDep):
    return await _my_task_view(session, await execution.found(session, actor, task_id))


class StageIn(BaseModel):
    checklist: list[bool] = Field(default_factory=list, max_length=50)


@router.post("/my/tasks/{task_id}/stages/{stage}/complete")
async def complete_stage(
    task_id: int, stage: int, body: StageIn, session: SessionDep, actor: ActorDep
):
    task = await execution.complete_stage(session, actor, task_id, stage, body.checklist)
    return await _my_task_view(session, task)


@router.post("/my/tasks/{task_id}/timer-reset")
async def timer_reset(task_id: int, body: ReasonIn, session: SessionDep, actor: ActorDep):
    return await _my_task_view(
        session, await execution.reset_timer(session, actor, task_id, body.reason)
    )


class ErrorIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/my/tasks/{task_id}/report-error")
async def report_error(task_id: int, body: ErrorIn, session: SessionDep, actor: ActorDep):
    task = await execution.report_error(session, actor, task_id, body.reason, body.comment)
    return await _my_task_view(session, task)


@router.post("/my/tasks/{task_id}/complete")
async def complete(task_id: int, session: SessionDep, actor: ActorDep):
    return await _my_task_view(session, await execution.complete(session, actor, task_id))


@router.post("/my/tasks/{task_id}/photos")
async def upload_photo(
    task_id: int,
    session: SessionDep,
    actor: ActorDep,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["stage", "final"], Form()],
    stage: Annotated[int | None, Form()] = None,
):
    data = await file.read(storage.MAX_PHOTO_BYTES + 1)
    photo, task = await photos.upload(
        session, actor, task_id, kind=PhotoKind(kind), stage=stage, data=data
    )
    view = await _my_task_view(session, task)
    view["uploaded"] = photo_out(photo)
    return view


@router.get("/photos/{photo_id}")
async def get_photo(photo_id: int, session: SessionDep, actor: ActorDep):
    photo = await photos.photo_for_viewer(session, actor, photo_id)
    path = storage.photo_path(photo.path)
    if not path.exists():
        raise NotFound("Файл фото не найден на диске")
    return FileResponse(
        path, media_type=photo.content_type, headers={"Cache-Control": "private, max-age=3600"}
    )


# --- проверка -----------------------------------------------------------------------


async def _review_list(
    session, actor, perm: Perm, status: TaskStatus, product_id: int | None, limit: int
):
    actor.require(perm)
    q = (
        select(Task, Product.name, User)
        .join(Product, Product.id == Task.product_id)
        .join(User, User.id == Task.assignee_id)
        .where(Task.status == status)
    )
    scope = await scope_for(session, actor, perm)
    if scope is not ALL:
        q = q.where(Task.assignee_id.in_(scope))
    if not actor.is_owner:
        q = q.where(Task.assignee_id != actor.id)
    if product_id:
        q = q.where(Task.product_id == product_id)
    rows = (
        await session.execute(q.order_by(Task.completed_at.nulls_last(), Task.id).limit(limit))
    ).all()
    task_ids = [t.id for t, _, _ in rows]
    pics: dict[int, list] = {}
    if task_ids:
        for p in (
            await session.execute(
                # На проверку — только фото текущей попытки исполнителя.
                select(TaskPhoto)
                .join(Task, Task.id == TaskPhoto.task_id)
                .where(
                    TaskPhoto.task_id.in_(task_ids),
                    TaskPhoto.attempt == Task.attempt,
                    TaskPhoto.user_id == Task.assignee_id,
                )
                .order_by(TaskPhoto.id)
            )
        ).scalars():
            pics.setdefault(p.task_id, []).append(photo_out(p))
    events: dict[int, list] = {}
    if task_ids and status == TaskStatus.MANUAL_REVIEW:
        for e in (
            await session.execute(
                select(TaskEvent)
                .where(
                    TaskEvent.task_id.in_(task_ids),
                    TaskEvent.kind.in_(["ocr_attempt", "error_reported", "manual_review"]),
                )
                .order_by(TaskEvent.id)
            )
        ).scalars():
            events.setdefault(e.task_id, []).append(
                {"kind": e.kind, "data": e.data, "created_at": e.created_at}
            )
    return [
        {
            "task": TaskOut.model_validate(t),
            "product_name": pname,
            "assignee": {"id": u.id, "full_name": u.full_name, "telegram_id": u.telegram_id},
            "photos": pics.get(t.id, []),
            "events": events.get(t.id, []),
        }
        for t, pname, u in rows
    ]


@router.get("/moderation")
async def moderation_queue(
    session: SessionDep, actor: ActorDep, product_id: int | None = None, limit: int = 50
):
    return await _review_list(
        session, actor, Perm.MODERATION_REVIEW, TaskStatus.MODERATION, product_id, min(limit, 200)
    )


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject", "return"]
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/moderation/{task_id}", response_model=TaskOut)
async def moderate(task_id: int, body: DecisionIn, session: SessionDep, actor: ActorDep):
    return await moderation.decide(session, actor, task_id, body.decision, body.reason)


@router.get("/manual-review")
async def manual_queue(
    session: SessionDep, actor: ActorDep, product_id: int | None = None, limit: int = 50
):
    return await _review_list(
        session,
        actor,
        Perm.OCR_MANUAL_REVIEW,
        TaskStatus.MANUAL_REVIEW,
        product_id,
        min(limit, 200),
    )


@router.post("/manual-review/{task_id}/{action}", response_model=TaskOut)
async def manual_action(
    task_id: int,
    action: Literal["approve", "return", "cancel"],
    body: ReasonIn,
    session: SessionDep,
    actor: ActorDep,
):
    fn = {
        "approve": moderation.manual_approve,
        "return": moderation.manual_return,
        "cancel": moderation.manual_cancel,
    }[action]
    return await fn(session, actor, task_id, body.reason)


@router.get("/tasks/{task_id}/photos", response_model=list[PhotoOut])
async def task_photos(task_id: int, session: SessionDep, actor: ActorDep):
    task = await session.get(Task, task_id)
    if task is None:
        raise NotFound("Задание не найдено")
    manager = actor.is_owner
    for perm in (Perm.MODERATION_REVIEW, Perm.OCR_MANUAL_REVIEW, Perm.TASKS_MANAGE):
        if manager:
            break
        if actor.has(perm) and task.assignee_id is not None and task.assignee_id != actor.id:
            scope = await scope_for(session, actor, perm)
            manager = scope is ALL or task.assignee_id in scope
    if not manager and task.assignee_id != actor.id:
        raise NotFound("Задание не найдено")
    # Исполнитель видит только свои фото текущей попытки, руководитель — всю историю.
    pics = await photos.task_photos(session, task, current_only=not manager)
    return [photo_out(p) for p in pics]
