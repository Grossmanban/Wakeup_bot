from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import ActorDep, SessionDep
from app.api.schemas_products import (
    GenderIn,
    IdsIn,
    ProductIn,
    ProductListItem,
    ProductOut,
    ProductPatch,
    RateIn,
    RatesOut,
    StatusIn,
    TaskTypeIn,
    TaskTypeOut,
    UserProductsOut,
    dump_settings,
)
from app.errors import AppError, Forbidden, NotFound
from app.models import (
    Product,
    ProductAccess,
    ProductNetwork,
    ProductRate,
    ProductStatus,
    Role,
    Task,
    TaskStatus,
    TaskType,
    User,
)
from app.permissions import Perm
from app.services import audit
from app.services import products as svc
from app.services.access import Actor
from app.services.audit import Category

router = APIRouter(tags=["products"])


def _require_products_view(actor: Actor) -> None:
    if not (actor.is_owner or actor.has(Perm.TASKS_MANAGE) or actor.has(Perm.TASKS_IMPORT)):
        raise Forbidden("no_permission", "Недостаточно прав для этого действия")


@router.get("/products", response_model=list[ProductListItem])
async def list_products(session: SessionDep, actor: ActorDep, include_deleted: bool = False):
    _require_products_view(actor)
    q = select(Product).order_by(Product.name)
    if not include_deleted:
        q = q.where(Product.status != ProductStatus.DELETED)
    products = list((await session.execute(q)).scalars())
    counts = {
        (pid, st): n
        for pid, st, n in await session.execute(
            select(Task.product_id, Task.status, func.count()).group_by(
                Task.product_id, Task.status
            )
        )
    }
    return [
        ProductListItem(
            id=p.id,
            name=p.name,
            status=p.status,
            pool=counts.get((p.id, TaskStatus.POOL), 0),
            active=counts.get((p.id, TaskStatus.ACTIVE), 0),
            on_moderation=counts.get((p.id, TaskStatus.MODERATION), 0),
        )
        for p in products
    ]


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, session: SessionDep, actor: ActorDep):
    _require_products_view(actor)
    return await svc.get_product(session, product_id)


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductIn, session: SessionDep, actor: ActorDep):
    return await svc.create_product(session, actor, dump_settings(body, partial=False))


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(product_id: int, body: ProductPatch, session: SessionDep, actor: ActorDep):
    return await svc.update_product(session, actor, product_id, dump_settings(body, partial=True))


@router.put("/products/{product_id}/status", response_model=ProductOut)
async def set_product_status(product_id: int, body: StatusIn, session: SessionDep, actor: ActorDep):
    return await svc.set_status(session, actor, product_id, body.status)


@router.get("/products/{product_id}/task-types", response_model=list[TaskTypeOut])
async def get_task_types(product_id: int, session: SessionDep, actor: ActorDep):
    _require_products_view(actor)
    await svc.get_product(session, product_id)
    rows = await session.execute(
        select(TaskType).where(TaskType.product_id == product_id).order_by(TaskType.id)
    )
    return list(rows.scalars())


@router.put("/products/{product_id}/task-types", response_model=list[TaskTypeOut])
async def put_task_types(
    product_id: int, body: list[TaskTypeIn], session: SessionDep, actor: ActorDep
):
    return await svc.set_task_types(session, actor, product_id, [t.model_dump() for t in body])


@router.get("/products/{product_id}/rates", response_model=RatesOut)
async def get_rates(product_id: int, session: SessionDep, actor: ActorDep):
    actor.require_owner()
    await svc.get_product(session, product_id)
    return await svc.rates_overview(session, product_id)


@router.put("/products/{product_id}/rates/roles/{role}", response_model=RatesOut)
async def set_role_rate(
    product_id: int, role: Role, body: RateIn, session: SessionDep, actor: ActorDep
):
    await svc.set_rate(session, actor, product_id, role=role, amount_kop=body.amount_kop)
    return await svc.rates_overview(session, product_id)


@router.put("/products/{product_id}/rates/users/{user_id}", response_model=RatesOut)
async def set_user_rate(
    product_id: int, user_id: int, body: RateIn, session: SessionDep, actor: ActorDep
):
    await svc.set_rate(session, actor, product_id, user_id=user_id, amount_kop=body.amount_kop)
    return await svc.rates_overview(session, product_id)


@router.get("/products/{product_id}/networks", response_model=list[int])
async def get_product_networks(product_id: int, session: SessionDep, actor: ActorDep):
    _require_products_view(actor)
    rows = await session.execute(
        select(ProductNetwork.network_id).where(ProductNetwork.product_id == product_id)
    )
    return sorted(rows.scalars())


@router.put("/products/{product_id}/networks", response_model=list[int])
async def put_product_networks(product_id: int, body: IdsIn, session: SessionDep, actor: ActorDep):
    return sorted(await svc.set_product_networks(session, actor, product_id, set(body.ids)))


@router.get("/users/{user_id}/products", response_model=UserProductsOut)
async def get_user_products(user_id: int, session: SessionDep, actor: ActorDep):
    actor.require_owner()
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("Сотрудник не найден")
    ids = sorted(
        (
            await session.execute(
                select(ProductAccess.product_id).where(ProductAccess.user_id == user_id)
            )
        ).scalars()
    )
    # Действующие индивидуальные ставки: последняя запись по каждому продукту.
    latest = (
        select(ProductRate.product_id, func.max(ProductRate.id).label("mid"))
        .where(ProductRate.user_id == user_id)
        .group_by(ProductRate.product_id)
        .subquery()
    )
    rates = [
        {"product_id": r.product_id, "amount_kop": r.amount_kop}
        for r in (
            await session.execute(select(ProductRate).join(latest, ProductRate.id == latest.c.mid))
        ).scalars()
        if r.amount_kop is not None
    ]
    return UserProductsOut(product_ids=ids, gender=user.gender, rates=rates)


@router.put("/users/{user_id}/products", response_model=list[int])
async def put_user_products(user_id: int, body: IdsIn, session: SessionDep, actor: ActorDep):
    return sorted(await svc.set_user_products(session, actor, user_id, set(body.ids)))


@router.put("/users/{user_id}/gender", response_model=UserProductsOut)
async def put_gender(user_id: int, body: GenderIn, session: SessionDep, actor: ActorDep):
    actor.require_owner()
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFound("Сотрудник не найден")
    if body.gender not in (None, "m", "f"):
        raise AppError("bad_gender", "Недопустимое значение")
    if user.gender != body.gender:
        audit.record(
            session,
            category=Category.USERS,
            action="profile.gender_changed",
            actor_id=actor.id,
            target_user_id=user.id,
            object_type="user",
            object_id=user.id,
            old={"gender": user.gender},
            new={"gender": body.gender},
        )
        user.gender = body.gender
        await session.commit()
    return await get_user_products(user_id, session, actor)
