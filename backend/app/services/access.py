"""Права и области видимости (ТЗ 2.2, 2.4). Правила — docs/permissions.md."""

from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, NotFound
from app.models import Role, User, UserNetwork, UserPermission
from app.permissions import Perm


class _All:
    """Без ограничений по людям."""

    def __repr__(self) -> str:
        return "ALL"


ALL = _All()
Scope = _All | frozenset[int]

# Роли, видимость которых всегда ограничена собственной командой.
TEAM_ROLES = (Role.COACH, Role.INSTRUCTOR)

# Действия, которые нельзя выполнять над собой: иначе можно начислить себе бонус и выплату,
# подтвердить своё ФИО или смену, переписать свои сети. Делает владелец или другой руководитель.
NOT_ON_SELF = frozenset(
    {
        Perm.NAME_CHANGES_REVIEW,
        Perm.USERS_STATUS,
        Perm.ORG_MANAGE,
        Perm.SCHEDULE_MANAGE,
        Perm.FINANCE_MANAGE,
        Perm.PAYOUTS_MANAGE,
        Perm.TRAINING_MANAGE,
        Perm.GROUPS_INVITE,
    }
)


@dataclass
class Actor:
    user: User
    # permission -> множество network_id; None в множестве = без ограничения по сети
    grants: dict[str, set[int | None]] = field(default_factory=dict)

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def is_owner(self) -> bool:
        return self.user.role == Role.OWNER

    def has(self, perm: Perm) -> bool:
        return self.is_owner or perm.value in self.grants

    def require(self, perm: Perm) -> None:
        if not self.has(perm):
            raise Forbidden("no_permission", "Недостаточно прав для этого действия")

    def require_owner(self) -> None:
        if not self.is_owner:
            raise Forbidden("owner_only", "Это действие доступно только владельцу")

    def has_global(self, perm: Perm) -> bool:
        return self.is_owner or None in self.grants.get(perm.value, set())

    def require_company_wide(self, perm: Perm) -> None:
        """Для настроек, действующих на всех: разрешение на все сети и роль без команды."""
        if not self.has_global(perm) or self.user.role in TEAM_ROLES:
            raise Forbidden(
                "company_wide_only",
                "Правило действует на всех сотрудников — нужно разрешение на все сети",
            )


async def load_actor(session: AsyncSession, user: User) -> Actor:
    rows = await session.execute(
        select(UserPermission.permission, UserPermission.network_id).where(
            UserPermission.user_id == user.id
        )
    )
    grants: dict[str, set[int | None]] = {}
    for perm, network_id in rows:
        grants.setdefault(perm, set()).add(network_id)
    return Actor(user=user, grants=grants)


async def team_of(session: AsyncSession, user_id: int) -> frozenset[int]:
    """Все подчинённые по иерархии, без повторов. UNION отсекает циклы."""
    rows = await session.execute(
        text(
            """
            WITH RECURSIVE team(id) AS (
                SELECT subordinate_id FROM supervisions WHERE supervisor_id = :uid
                UNION
                SELECT s.subordinate_id FROM supervisions s JOIN team t ON s.supervisor_id = t.id
            )
            SELECT id FROM team
            """
        ),
        {"uid": user_id},
    )
    return frozenset(r[0] for r in rows)


async def _network_members(session: AsyncSession, network_ids: set[int]) -> frozenset[int]:
    rows = await session.execute(
        select(UserNetwork.user_id).where(UserNetwork.network_id.in_(network_ids))
    )
    return frozenset(r[0] for r in rows)


async def scope_for(session: AsyncSession, actor: Actor, perm: Perm) -> Scope:
    """Над кем actor может выполнять действие perm.

    Область может включать самого actor (он состоит в своей сети); запрет действий над собой —
    в get_target и NOT_ON_SELF.
    """
    if actor.is_owner:
        return ALL

    networks = actor.grants.get(perm.value)
    team_role = actor.user.role in TEAM_ROLES

    # Тренер и инструктор видят свою команду без отдельного разрешения (ТЗ 2.4).
    if networks is None:
        if perm == Perm.USERS_VIEW and team_role:
            return await team_of(session, actor.id)
        return frozenset()

    if None in networks:
        base: Scope = ALL
    else:
        base = await _network_members(session, {n for n in networks if n is not None})

    if not team_role:
        return base
    team = await team_of(session, actor.id)
    return team if base is ALL else team & base


def in_scope(scope: Scope, user_id: int) -> bool:
    return scope is ALL or user_id in scope


async def get_target(session: AsyncSession, actor: Actor, perm: Perm, user_id: int) -> User:
    """Найти человека, над которым actor выполняет perm.

    Чужие для actor люди дают 404, а не 403: так нельзя перебором узнать,
    какие ID существуют (защита от IDOR).
    """
    scope = await scope_for(session, actor, perm)
    if scope == frozenset() and not actor.has(perm):
        raise Forbidden("no_permission", "Недостаточно прав для этого действия")
    if perm in NOT_ON_SELF and user_id == actor.id and not actor.is_owner:
        raise Forbidden("self_action", "Это действие нельзя выполнить над собой")
    if not in_scope(scope, user_id):
        raise NotFound("Сотрудник не найден")
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("Сотрудник не найден")
    return user
