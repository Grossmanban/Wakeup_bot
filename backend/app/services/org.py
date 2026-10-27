"""Сети и иерархия «владелец → сеть → тренер → инструктор → сотрудники» (ТЗ 2.4)."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, Conflict, Forbidden, NotFound
from app.models import AccountStatus, Network, Role, Supervision, User, UserNetwork
from app.permissions import Perm
from app.services import audit
from app.services.access import Actor, get_target
from app.services.audit import Category

# Кто кем может руководить.
ALLOWED_SUBORDINATES = {
    Role.COACH: (Role.INSTRUCTOR, Role.EMPLOYEE),
    Role.INSTRUCTOR: (Role.EMPLOYEE,),
}


async def create_network(session: AsyncSession, actor: Actor, name: str) -> Network:
    if not actor.has_global(Perm.ORG_MANAGE):
        raise Forbidden(
            "no_permission", "Создавать сети может владелец или админ без ограничения по сети"
        )
    name = " ".join(name.split())
    if not name:
        raise AppError("invalid_name", "Укажите название сети")
    exists = await session.scalar(select(Network.id).where(Network.name == name))
    if exists:
        raise Conflict("duplicate", "Сеть с таким названием уже есть")
    net = Network(name=name)
    session.add(net)
    await session.flush()
    audit.record(
        session,
        category=Category.SETTINGS,
        action="network.created",
        actor_id=actor.id,
        object_type="network",
        object_id=net.id,
        new={"name": name},
    )
    await session.commit()
    return net


async def update_network(
    session: AsyncSession, actor: Actor, network_id: int, *, name: str | None, archived: bool | None
) -> Network:
    if not actor.has_global(Perm.ORG_MANAGE):
        raise Forbidden(
            "no_permission", "Изменять сети может владелец или админ без ограничения по сети"
        )
    net = await session.get(Network, network_id, with_for_update=True)
    if net is None:
        raise NotFound("Сеть не найдена")
    old = {"name": net.name, "is_archived": net.is_archived}
    if name is not None:
        name = " ".join(name.split())
        if not name:
            raise AppError("invalid_name", "Укажите название сети")
        clash = await session.scalar(
            select(Network.id).where(Network.name == name, Network.id != net.id)
        )
        if clash:
            raise Conflict("duplicate", "Сеть с таким названием уже есть")
        net.name = name
    if archived is not None:
        net.is_archived = archived
    new = {"name": net.name, "is_archived": net.is_archived}
    if new != old:
        audit.record(
            session,
            category=Category.SETTINGS,
            action="network.updated",
            actor_id=actor.id,
            object_type="network",
            object_id=net.id,
            old=old,
            new=new,
        )
    await session.commit()
    return net


def _manageable_networks(actor: Actor) -> set[int] | None:
    """None — любые сети."""
    if actor.has_global(Perm.ORG_MANAGE):
        return None
    return {n for n in actor.grants.get(Perm.ORG_MANAGE.value, set()) if n is not None}


async def set_user_networks(
    session: AsyncSession, actor: Actor, user_id: int, network_ids: set[int]
) -> set[int]:
    # Область по команде и сетям: админ с ограничением по сети не может «присвоить»
    # человека без сети, тренер — человека не из своей команды.
    await get_target(session, actor, Perm.ORG_MANAGE, user_id)
    allowed = _manageable_networks(actor)
    user = await session.get(User, user_id, with_for_update=True)
    if user is None or user.status in (AccountStatus.UNREGISTERED, AccountStatus.PENDING):
        raise NotFound("Сотрудник не найден")
    if network_ids:
        found = set(
            (await session.execute(select(Network.id).where(Network.id.in_(network_ids)))).scalars()
        )
        if missing := network_ids - found:
            raise AppError("bad_network", f"Сеть не найдена: {sorted(missing)}")

    current = set(
        (
            await session.execute(
                select(UserNetwork.network_id).where(UserNetwork.user_id == user.id)
            )
        ).scalars()
    )
    added, removed = network_ids - current, current - network_ids
    if allowed is not None:
        # Админ с ограничением по сети меняет только свои сети и только у людей из них.
        if not current & allowed:
            raise NotFound("Сотрудник не найден")
        if (added | removed) - allowed:
            raise Forbidden("no_permission", "Нет прав на одну из выбранных сетей")

    if removed:
        await session.execute(
            delete(UserNetwork).where(
                UserNetwork.user_id == user.id, UserNetwork.network_id.in_(removed)
            )
        )
    for n in added:
        session.add(UserNetwork(user_id=user.id, network_id=n, created_by_id=actor.id))
    if added or removed:
        audit.record(
            session,
            category=Category.USERS,
            action="networks.changed",
            actor_id=actor.id,
            target_user_id=user.id,
            object_type="user",
            object_id=user.id,
            old=sorted(current),
            new=sorted(network_ids),
        )
    await session.commit()
    return network_ids


async def add_supervision(
    session: AsyncSession, actor: Actor, supervisor_id: int, subordinate_id: int
) -> None:
    sup = await get_target(session, actor, Perm.ORG_MANAGE, supervisor_id)
    sub = await get_target(session, actor, Perm.ORG_MANAGE, subordinate_id)
    if sub.role not in ALLOWED_SUBORDINATES.get(sup.role, ()):
        raise AppError(
            "bad_hierarchy",
            "Руководить может тренер (инструкторами и сотрудниками) или инструктор (сотрудниками)",
        )
    for u in (sup, sub):
        if u.status != AccountStatus.ACTIVE:
            raise Conflict("not_active", f"Аккаунт {u.full_name} не активен")
    res = await session.execute(
        insert(Supervision)
        .values(supervisor_id=sup.id, subordinate_id=sub.id, created_by_id=actor.id)
        .on_conflict_do_nothing()
    )
    if res.rowcount:
        audit.record(
            session,
            category=Category.USERS,
            action="supervision.added",
            actor_id=actor.id,
            target_user_id=sub.id,
            object_type="supervision",
            object_id=f"{sup.id}:{sub.id}",
            new={"supervisor_id": sup.id, "subordinate_id": sub.id},
        )
    await session.commit()


async def remove_supervision(
    session: AsyncSession, actor: Actor, supervisor_id: int, subordinate_id: int
) -> None:
    await get_target(session, actor, Perm.ORG_MANAGE, supervisor_id)
    await get_target(session, actor, Perm.ORG_MANAGE, subordinate_id)
    res = await session.execute(
        delete(Supervision).where(
            Supervision.supervisor_id == supervisor_id,
            Supervision.subordinate_id == subordinate_id,
        )
    )
    if res.rowcount:
        audit.record(
            session,
            category=Category.USERS,
            action="supervision.removed",
            actor_id=actor.id,
            target_user_id=subordinate_id,
            object_type="supervision",
            object_id=f"{supervisor_id}:{subordinate_id}",
            old={"supervisor_id": supervisor_id, "subordinate_id": subordinate_id},
        )
    await session.commit()
