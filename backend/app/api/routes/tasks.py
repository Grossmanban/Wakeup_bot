from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Query, UploadFile
from sqlalchemy import func, or_, select

from app.api.deps import ActorDep, SessionDep
from app.api.schemas_products import (
    ImportPreviewOut,
    MyProduct,
    ReasonIn,
    TaskListItem,
    TaskOut,
)
from app.errors import AppError, Conflict, NotFound
from app.models import (
    ImportBatch,
    Product,
    ProductAccess,
    ProductStatus,
    Task,
    TaskEvent,
    TaskStatus,
    User,
)
from app.permissions import Perm
from app.services import idempotency, imports
from app.services import products as products_svc
from app.services import tasks as tasks_svc
from app.services.access import ALL, scope_for

router = APIRouter(tags=["tasks"])

IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


# --- импорт -------------------------------------------------------------------------


@router.post("/imports", response_model=ImportPreviewOut)
async def upload_import(
    session: SessionDep,
    actor: ActorDep,
    file: Annotated[UploadFile, File()],
    product_id: Annotated[int | None, Form()] = None,
):
    content = await file.read(imports.MAX_FILE_BYTES + 1)
    batch = await imports.preview(
        session,
        actor,
        file_name=file.filename or "import.xlsx",
        content=content,
        product_id=product_id,
    )
    return ImportPreviewOut.model_validate(batch, from_attributes=True)


@router.get("/imports/{batch_id}", response_model=ImportPreviewOut)
async def get_import(batch_id: int, session: SessionDep, actor: ActorDep):
    actor.require(Perm.TASKS_IMPORT)
    batch = await session.get(ImportBatch, batch_id)
    if batch is None or (batch.uploaded_by_id != actor.id and not actor.is_owner):
        raise NotFound("Загрузка не найдена")
    return ImportPreviewOut.model_validate(batch, from_attributes=True)


@router.post("/imports/{batch_id}/commit")
async def commit_import(batch_id: int, session: SessionDep, actor: ActorDep):
    return await imports.commit(session, actor, batch_id)


@router.post("/imports/{batch_id}/discard", status_code=204)
async def discard_import(batch_id: int, session: SessionDep, actor: ActorDep):
    await imports.discard(session, actor, batch_id)


# --- управление заданиями ------------------------------------------------------------


@router.get("/tasks")
async def list_tasks(
    session: SessionDep,
    actor: ActorDep,
    product_id: int | None = None,
    status: Annotated[list[TaskStatus] | None, Query()] = None,
    assignee_id: int | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    actor.require(Perm.TASKS_MANAGE)
    query = (
        select(Task, Product.name, User)
        .join(Product, Product.id == Task.product_id)
        .outerjoin(User, User.id == Task.assignee_id)
    )
    scope = await scope_for(session, actor, Perm.TASKS_MANAGE)
    if scope is not ALL:
        # Ограниченный по области видит задания своих людей и общий пул.
        query = query.where(or_(Task.assignee_id.in_(scope), Task.assignee_id.is_(None)))
    if product_id:
        query = query.where(Task.product_id == product_id)
    if status:
        query = query.where(Task.status.in_(status))
    if assignee_id:
        query = query.where(Task.assignee_id == assignee_id)
    if q:
        query = query.where(Task.external_id.ilike(f"%{q.strip()}%"))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.execute(query.order_by(Task.id.desc()).limit(limit).offset(offset))
    items = [
        TaskListItem(
            **TaskOut.model_validate(t).model_dump(),
            product_name=pname,
            assignee_name=u.full_name if u else None,
        )
        for t, pname, u in rows
    ]
    return {"items": items, "total": total or 0}


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, session: SessionDep, actor: ActorDep):
    task = await tasks_svc.get_task_for_manager(session, actor, task_id)
    events = (
        await session.execute(
            select(TaskEvent, User)
            .outerjoin(User, User.id == TaskEvent.user_id)
            .where(TaskEvent.task_id == task.id)
            .order_by(TaskEvent.id)
        )
    ).all()
    product = await session.get(Product, task.product_id)
    assignee = await session.get(User, task.assignee_id) if task.assignee_id else None
    return {
        "task": TaskOut.model_validate(task),
        "product_name": product.name,
        "assignee_name": assignee.full_name if assignee else None,
        "events": [
            {
                "id": e.id,
                "kind": e.kind,
                "stage": e.stage,
                "data": e.data,
                "created_at": e.created_at,
                "user_name": u.full_name if u else None,
            }
            for e, u in events
        ],
    }


@router.post("/tasks/{task_id}/return-to-pool", response_model=TaskOut)
async def return_to_pool(task_id: int, body: ReasonIn, session: SessionDep, actor: ActorDep):
    return await tasks_svc.return_to_pool(session, actor, task_id, body.reason)


@router.post("/tasks/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: int, body: ReasonIn, session: SessionDep, actor: ActorDep):
    return await tasks_svc.cancel_task(session, actor, task_id, body.reason)


# --- сотрудник ----------------------------------------------------------------------


@router.get("/my/products", response_model=list[MyProduct])
async def my_products(session: SessionDep, actor: ActorDep):
    user = actor.user
    products = list(
        (
            await session.execute(
                select(Product)
                .join(ProductAccess, ProductAccess.product_id == Product.id)
                .where(
                    ProductAccess.user_id == user.id, Product.status.in_(products_svc.LIVE_STATUSES)
                )
                .order_by(Product.name)
            )
        ).scalars()
    )
    pool = await tasks_svc.pool_counts(session, {p.id for p in products}, user)
    active = dict(
        (
            await session.execute(
                select(Task.product_id, func.count())
                .where(Task.assignee_id == user.id, Task.status == TaskStatus.ACTIVE)
                .group_by(Task.product_id)
            )
        ).all()
    )
    out = []
    for p in products:
        try:
            rate, err = await products_svc.resolve_rate(session, user, p.id), None
        except AppError as e:
            rate, err = None, e.message
        out.append(
            MyProduct(
                id=p.id,
                name=p.name,
                description=p.description,
                instructions=p.instructions,
                links=p.links,
                materials=p.materials,
                status=p.status,
                pool=pool.get(p.id, 0) if p.status == ProductStatus.ACTIVE else 0,
                active=active.get(p.id, 0),
                active_task_limit=p.active_task_limit,
                rate_kop=rate,
                rate_error=err,
            )
        )
    return out


@router.post("/my/products/{product_id}/take", response_model=TaskOut)
async def take_task(
    product_id: int, session: SessionDep, actor: ActorDep, idem_key: IdemKey = None
):
    scope = f"take:{product_id}"
    if (saved := await idempotency.lookup(session, actor.id, idem_key, scope)) is not None:
        task = await session.get(Task, saved["task_id"])
        # Повтор запроса возвращает задание, только пока оно ещё за этим сотрудником.
        if task is None or task.assignee_id != actor.id:
            raise Conflict("task_gone", "Это задание уже не за вами — возьмите новое")
        return task
    return await tasks_svc.take_task(session, actor, product_id, idem_key=idem_key)


@router.get("/my/tasks", response_model=list[TaskListItem])
async def my_tasks(
    session: SessionDep,
    actor: ActorDep,
    status: Annotated[list[TaskStatus] | None, Query()] = None,
    limit: int = Query(50, ge=1, le=200),
):
    statuses = status or [TaskStatus.ACTIVE, TaskStatus.MANUAL_REVIEW]
    rows = await session.execute(
        select(Task, Product.name)
        .join(Product, Product.id == Task.product_id)
        .where(Task.assignee_id == actor.id, Task.status.in_(statuses))
        .order_by(Task.assigned_at.desc().nulls_last(), Task.id.desc())
        .limit(limit)
    )
    return [
        TaskListItem(**TaskOut.model_validate(t).model_dump(), product_name=n, assignee_name=None)
        for t, n in rows
    ]
