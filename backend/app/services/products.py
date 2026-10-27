"""Продукты, типы заданий, ставки и продуктовые доступы (ТЗ 4, 11.2)."""

from html import escape
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, Conflict, NotFound
from app.models import (
    AccountStatus,
    Network,
    Product,
    ProductAccess,
    ProductNetwork,
    ProductRate,
    ProductStatus,
    Role,
    TaskType,
    User,
)
from app.services import audit, outbox
from app.services.access import Actor
from app.services.audit import Category

# Продукты, по которым ещё может идти работа.
LIVE_STATUSES = (ProductStatus.ACTIVE, ProductStatus.HIDDEN)

SETTINGS_FIELDS = (
    "name",
    "description",
    "active_task_limit",
    "instructions",
    "links",
    "materials",
    "required_fields",
    "gender",
    "expected_text_default",
    "flow",
    "final_photo_required",
    "timer_reset_reason_required",
    "error_reasons",
    "allow_return_for_fix",
    "ocr_enabled",
    "ocr_rule",
    "ocr_threshold",
    "ocr_attempts",
    "training_volume",
    "min_sample_units",
)


async def get_product(session: AsyncSession, product_id: int, *, lock: bool = False) -> Product:
    p = await session.get(Product, product_id, with_for_update=lock)
    if p is None or p.status == ProductStatus.DELETED:
        raise NotFound("Продукт не найден")
    return p


async def _check_name(session: AsyncSession, name: str, exclude_id: int | None = None) -> str:
    name = " ".join(name.split())
    if not name:
        raise AppError("invalid_name", "Укажите название продукта")
    q = select(Product.id).where(func.lower(Product.name) == name.lower())
    if exclude_id:
        q = q.where(Product.id != exclude_id)
    if await session.scalar(q):
        raise Conflict("duplicate", "Продукт с таким названием уже есть")
    return name


def _snapshot(p: Product, fields=SETTINGS_FIELDS) -> dict[str, Any]:
    return {f: getattr(p, f) for f in fields}


async def create_product(session: AsyncSession, actor: Actor, data: dict[str, Any]) -> Product:
    actor.require_owner()
    data = dict(data)
    data["name"] = await _check_name(session, data["name"])
    p = Product(**data)
    session.add(p)
    await session.flush()
    audit.record(
        session,
        category=Category.SETTINGS,
        action="product.created",
        actor_id=actor.id,
        object_type="product",
        object_id=p.id,
        new=_snapshot(p, data.keys()),
    )
    await session.commit()
    return p


async def update_product(
    session: AsyncSession, actor: Actor, product_id: int, changes: dict[str, Any]
) -> Product:
    actor.require_owner()
    p = await get_product(session, product_id, lock=True)
    if "name" in changes:
        changes["name"] = await _check_name(session, changes["name"], p.id)
    old, new = {}, {}
    for k, v in changes.items():
        if k not in SETTINGS_FIELDS:
            continue
        if getattr(p, k) != v:
            old[k], new[k] = getattr(p, k), v
            setattr(p, k, v)
    if new:
        audit.record(
            session,
            category=Category.SETTINGS,
            action="product.updated",
            actor_id=actor.id,
            object_type="product",
            object_id=p.id,
            old=old,
            new=new,
        )
    await session.commit()
    return p


async def set_status(
    session: AsyncSession, actor: Actor, product_id: int, status: ProductStatus
) -> Product:
    actor.require_owner()
    p = await get_product(session, product_id, lock=True)
    if p.status == status:
        return p
    old = p.status
    p.status = status
    audit.record(
        session,
        category=Category.SETTINGS,
        action="product.status_changed",
        actor_id=actor.id,
        object_type="product",
        object_id=p.id,
        old={"status": old},
        new={"status": status},
    )
    await session.commit()
    return p


# --- типы заданий -------------------------------------------------------------------


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


async def set_task_types(
    session: AsyncSession, actor: Actor, product_id: int, types: list[dict[str, Any]]
) -> list[TaskType]:
    """Сохранить список типов. Типы, которых нет в списке, отключаются (не удаляются)."""
    actor.require_owner()
    p = await get_product(session, product_id, lock=True)
    existing = {
        t.id: t
        for t in (
            await session.execute(select(TaskType).where(TaskType.product_id == p.id))
        ).scalars()
    }
    seen_names: dict[str, str] = {}
    for t in types:
        name = " ".join(t["name"].split())
        if not name:
            raise AppError("invalid_name", "У типа задания должно быть название")
        for alias in [name, *t.get("excel_names", [])]:
            key = _norm(alias)
            if not key:
                continue
            if key in seen_names and seen_names[key] != name:
                raise AppError(
                    "duplicate_alias",
                    f"Название «{alias}» используется в двух типах: {seen_names[key]} и {name}",
                )
            seen_names[key] = name

    before = [
        {
            "id": t.id,
            "name": t.name,
            "excel_names": t.excel_names,
            "units": t.units,
            "is_active": t.is_active,
        }
        for t in existing.values()
    ]
    keep: set[int] = set()
    for t in types:
        excel = sorted({" ".join(a.split()) for a in t.get("excel_names", []) if a.strip()})
        values = {
            "name": " ".join(t["name"].split()),
            "excel_names": excel,
            "units": t.get("units", 1),
            "is_active": t.get("is_active", True),
        }
        if t.get("id"):
            row = existing.get(t["id"])
            if row is None:
                raise NotFound("Тип задания не найден")
            for k, v in values.items():
                setattr(row, k, v)
            keep.add(row.id)
        else:
            row = TaskType(product_id=p.id, **values)
            session.add(row)
    for tid, row in existing.items():
        if tid not in keep:
            row.is_active = False
    await session.flush()
    rows = list(
        (
            await session.execute(
                select(TaskType).where(TaskType.product_id == p.id).order_by(TaskType.id)
            )
        ).scalars()
    )
    audit.record(
        session,
        category=Category.SETTINGS,
        action="product.task_types_changed",
        actor_id=actor.id,
        object_type="product",
        object_id=p.id,
        old=before,
        new=[
            {
                "id": t.id,
                "name": t.name,
                "excel_names": t.excel_names,
                "units": t.units,
                "is_active": t.is_active,
            }
            for t in rows
        ],
    )
    await session.commit()
    return rows


# --- ставки -------------------------------------------------------------------------


async def set_rate(
    session: AsyncSession,
    actor: Actor,
    product_id: int,
    *,
    role: Role | None = None,
    user_id: int | None = None,
    amount_kop: int | None,
) -> ProductRate:
    """Новая ставка добавляется в историю; старая запись не изменяется."""
    actor.require_owner()
    if (role is None) == (user_id is None):
        raise AppError("bad_rate", "Укажите роль или сотрудника")
    if role == Role.OWNER:
        raise AppError("bad_rate", "Для роли владельца ставка не задаётся")
    if amount_kop is not None and amount_kop < 0:
        raise AppError("bad_rate", "Ставка не может быть отрицательной")
    p = await get_product(session, product_id, lock=True)
    if user_id is not None and await session.get(User, user_id) is None:
        raise NotFound("Сотрудник не найден")
    current = await _latest(session, p.id, role=role, user_id=user_id)
    row = ProductRate(
        product_id=p.id, role=role, user_id=user_id, amount_kop=amount_kop, created_by_id=actor.id
    )
    session.add(row)
    await session.flush()
    audit.record(
        session,
        category=Category.SETTINGS,
        action="rate.changed",
        actor_id=actor.id,
        target_user_id=user_id,
        object_type="product",
        object_id=p.id,
        old={"role": role, "user_id": user_id, "amount_kop": current},
        new={"role": role, "user_id": user_id, "amount_kop": amount_kop},
    )
    await session.commit()
    return row


async def _latest(
    session: AsyncSession, product_id: int, *, role: Role | None, user_id: int | None
) -> int | None:
    q = select(ProductRate.amount_kop).where(ProductRate.product_id == product_id)
    if user_id is not None:
        q = q.where(ProductRate.user_id == user_id)
    else:
        q = q.where(ProductRate.role == role)
    return await session.scalar(q.order_by(ProductRate.id.desc()).limit(1))


async def resolve_rate(session: AsyncSession, user: User, product_id: int) -> int:
    """Ставка за единицу: индивидуальная → ставка роли. Нулевая ставка молча не подставляется."""
    individual = await _latest(session, product_id, role=None, user_id=user.id)
    if individual is not None:
        return individual
    by_role = await _latest(session, product_id, role=user.role, user_id=None)
    if by_role is not None:
        return by_role
    product = await session.get(Product, product_id)
    name = product.name if product else product_id
    raise AppError(
        "rate_not_configured",
        f"Не настроена ставка по продукту «{name}». Обратитесь к администрации",
        409,
    )


async def rates_overview(session: AsyncSession, product_id: int) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(ProductRate, User)
            .outerjoin(User, User.id == ProductRate.user_id)
            .where(ProductRate.product_id == product_id)
            .order_by(ProductRate.id)
        )
    ).all()
    roles: dict[str, int | None] = {}
    users: dict[int, dict[str, Any]] = {}
    history = []
    for r, u in rows:
        if r.role is not None:
            roles[r.role.value] = r.amount_kop
        else:
            users[r.user_id] = {
                "user_id": r.user_id,
                "full_name": u.full_name,
                "amount_kop": r.amount_kop,
            }
        history.append(
            {
                "id": r.id,
                "role": r.role,
                "user_id": r.user_id,
                "user_name": u.full_name if u else None,
                "amount_kop": r.amount_kop,
                "created_at": r.created_at,
            }
        )
    return {
        "roles": {k: v for k, v in roles.items() if v is not None},
        "users": [v for v in users.values() if v["amount_kop"] is not None],
        "history": list(reversed(history)),
    }


# --- доступы ------------------------------------------------------------------------


async def user_product_ids(session: AsyncSession, user_id: int) -> set[int]:
    rows = await session.execute(
        select(ProductAccess.product_id)
        .join(Product, Product.id == ProductAccess.product_id)
        .where(ProductAccess.user_id == user_id, Product.status.in_(LIVE_STATUSES))
    )
    return set(rows.scalars())


async def set_user_products(
    session: AsyncSession, actor: Actor, user_id: int, product_ids: set[int]
) -> set[int]:
    """Только владелец выдаёт и отзывает продуктовые доступы (ТЗ 2.3)."""
    actor.require_owner()
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFound("Сотрудник не найден")
    if user.status not in (AccountStatus.ACTIVE, AccountStatus.DISABLED, AccountStatus.BLOCKED):
        raise Conflict("not_registered", "Сначала нужно одобрить регистрацию")
    products = {
        p.id: p
        for p in (
            await session.execute(select(Product).where(Product.id.in_(product_ids or {0})))
        ).scalars()
    }
    if missing := product_ids - products.keys():
        raise NotFound(f"Продукт не найден: {sorted(missing)}")
    if any(products[i].status == ProductStatus.DELETED for i in product_ids):
        raise Conflict("product_deleted", "Нельзя выдать доступ к удалённому продукту")

    current = set(
        (
            await session.execute(
                select(ProductAccess.product_id).where(ProductAccess.user_id == user.id)
            )
        ).scalars()
    )
    added, removed = product_ids - current, current - product_ids
    if removed:
        await session.execute(
            delete(ProductAccess).where(
                ProductAccess.user_id == user.id, ProductAccess.product_id.in_(removed)
            )
        )
    for pid in added:
        session.add(ProductAccess(user_id=user.id, product_id=pid, granted_by_id=actor.id))
    if added or removed:
        ev = audit.record(
            session,
            category=Category.USERS,
            action="product_access.changed",
            actor_id=actor.id,
            target_user_id=user.id,
            object_type="user",
            object_id=user.id,
            old=sorted(current),
            new=sorted(product_ids),
        )
        await session.flush()
        if added:
            names = ", ".join(escape(products[i].name) for i in sorted(added))
            await outbox.enqueue(
                session,
                dedup_key=f"audit:{ev.id}",
                chat_id=user.telegram_id,
                related_user_id=user.id,
                text=f"Вам открыт доступ к продуктам: {names}",
                open_app_button="Открыть приложение",
            )
    await session.commit()
    return product_ids


async def set_product_networks(
    session: AsyncSession, actor: Actor, product_id: int, network_ids: set[int]
) -> set[int]:
    actor.require_owner()
    p = await get_product(session, product_id, lock=True)
    found = set(
        (
            await session.execute(select(Network.id).where(Network.id.in_(network_ids or {0})))
        ).scalars()
    )
    if missing := network_ids - found:
        raise NotFound(f"Сеть не найдена: {sorted(missing)}")
    current = set(
        (
            await session.execute(
                select(ProductNetwork.network_id).where(ProductNetwork.product_id == p.id)
            )
        ).scalars()
    )
    if current == network_ids:
        return current
    await session.execute(delete(ProductNetwork).where(ProductNetwork.product_id == p.id))
    for n in network_ids:
        session.add(ProductNetwork(product_id=p.id, network_id=n))
    audit.record(
        session,
        category=Category.SETTINGS,
        action="product.networks_changed",
        actor_id=actor.id,
        object_type="product",
        object_id=p.id,
        old=sorted(current),
        new=sorted(network_ids),
    )
    await session.commit()
    return network_ids
