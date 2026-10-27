from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.api.deps import ActorDep, SessionDep
from app.api.schemas import (
    AppAccessIn,
    AuditOut,
    CatalogItem,
    Grant,
    NameChangeOut,
    NetworkOut,
    Page,
    PermissionsIn,
    PermissionsOut,
    RoleIn,
    StatusIn,
    UserOut,
    UserProfileOut,
    UserShort,
)
from app.errors import AppError
from app.models import (
    AccountStatus,
    AuditEvent,
    GroupMembership,
    NameChangeRequest,
    Network,
    RequestStatus,
    Role,
    Supervision,
    Task,
    TaskStatus,
    TgGroup,
    User,
    UserNetwork,
    UserPermission,
)
from app.permissions import PERM_TITLES, Perm
from app.services import candidates as candidates_svc
from app.services import groups as groups_svc
from app.services import moderation
from app.services import settings as settings_svc
from app.services import users as users_svc
from app.services.access import get_target

router = APIRouter(tags=["users"])


@router.get("/users", response_model=Page[UserShort])
async def list_users(
    session: SessionDep,
    actor: ActorDep,
    q: str | None = None,
    role: Role | None = None,
    status: AccountStatus | None = None,
    network_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = await users_svc.visible_users_query(session, actor)
    if q:
        like = f"%{q.strip().lstrip('@')}%"
        conds = [
            User.first_name.ilike(like),
            User.last_name.ilike(like),
            User.username.ilike(like),
        ]
        if q.strip().isdigit():
            conds.append(User.telegram_id == int(q.strip()))
        query = query.where(or_(*conds))
    if role:
        query = query.where(User.role == role)
    if status:
        query = query.where(User.status == status)
    if network_id:
        query = query.where(
            User.id.in_(select(UserNetwork.user_id).where(UserNetwork.network_id == network_id))
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.execute(
        query.order_by(User.last_name.nulls_last(), User.first_name, User.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(items=[UserShort.model_validate(u) for u in rows.scalars()], total=total or 0)


@router.get("/users/{user_id}", response_model=UserProfileOut)
async def get_user(user_id: int, session: SessionDep, actor: ActorDep):
    user = await get_target(session, actor, Perm.USERS_VIEW, user_id)
    networks = (
        await session.execute(
            select(Network)
            .join(UserNetwork, UserNetwork.network_id == Network.id)
            .where(UserNetwork.user_id == user.id)
            .order_by(Network.name)
        )
    ).scalars()
    supervisors = (
        await session.execute(
            select(User)
            .join(Supervision, Supervision.supervisor_id == User.id)
            .where(Supervision.subordinate_id == user.id)
        )
    ).scalars()
    subordinates = (
        await session.execute(
            select(User)
            .join(Supervision, Supervision.subordinate_id == User.id)
            .where(Supervision.supervisor_id == user.id)
        )
    ).scalars()
    grants = None
    if actor.is_owner:
        grants = [
            Grant(permission=p, network_id=n)
            for p, n in await session.execute(
                select(UserPermission.permission, UserPermission.network_id)
                .where(UserPermission.user_id == user.id)
                .order_by(UserPermission.permission)
            )
        ]
    pending = await session.scalar(
        select(NameChangeRequest).where(
            NameChangeRequest.user_id == user.id,
            NameChangeRequest.status == RequestStatus.PENDING,
        )
    )
    return UserProfileOut(
        user=UserOut.model_validate(user),
        networks=[NetworkOut.model_validate(n) for n in networks],
        supervisors=[UserShort.model_validate(u) for u in supervisors],
        subordinates=[UserShort.model_validate(u) for u in subordinates],
        grants=grants,
        pending_name_change=NameChangeOut.model_validate(pending) if pending else None,
    )


@router.put("/users/{user_id}/role", response_model=UserOut)
async def set_role(user_id: int, body: RoleIn, session: SessionDep, actor: ActorDep):
    return await users_svc.set_role(session, actor, user_id, body.role)


@router.put("/users/{user_id}/status", response_model=UserOut)
async def set_status(user_id: int, body: StatusIn, session: SessionDep, actor: ActorDep):
    return await users_svc.set_status(session, actor, user_id, body.status, body.reason)


@router.put("/users/{user_id}/app-access", response_model=UserOut)
async def set_app_access(user_id: int, body: AppAccessIn, session: SessionDep, actor: ActorDep):
    return await users_svc.set_app_access(session, actor, user_id, body.enabled)


@router.get("/permissions/catalog", response_model=list[CatalogItem])
async def permissions_catalog(actor: ActorDep):
    actor.require_owner()
    return [CatalogItem(code=p.value, title=PERM_TITLES[p]) for p in Perm]


@router.put("/users/{user_id}/permissions", response_model=PermissionsOut)
async def set_permissions(user_id: int, body: PermissionsIn, session: SessionDep, actor: ActorDep):
    grants: set[tuple[Perm, int | None]] = set()
    for g in body.grants:
        try:
            grants.add((Perm(g.permission), g.network_id))
        except ValueError as e:
            raise AppError("bad_permission", f"Неизвестное разрешение: {g.permission}") from e
    result = await users_svc.set_permissions(session, actor, user_id, grants)
    return PermissionsOut(
        grants=[
            Grant(permission=p, network_id=n)
            for p, n in sorted(result, key=lambda g: (g[0], g[1] or 0))
        ]
    )


@router.get("/users/{user_id}/history", response_model=list[AuditOut])
async def user_history(
    user_id: int,
    session: SessionDep,
    actor: ActorDep,
    limit: int = Query(100, ge=1, le=500),
):
    user = await get_target(session, actor, Perm.AUDIT_VIEW, user_id)
    rows = await session.execute(
        select(AuditEvent)
        .where(AuditEvent.target_user_id == user.id)
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    )
    return list(rows.scalars())


@router.get("/users/{user_id}/summary")
async def user_summary(user_id: int, session: SessionDep, actor: ActorDep):
    """Сводка для карточки сотрудника (ТЗ 3): задания, качество, обучение, группы, реферал."""
    user = await get_target(session, actor, Perm.USERS_VIEW, user_id)
    counts = dict(
        (
            await session.execute(
                select(Task.status, func.count())
                .where(Task.assignee_id == user.id)
                .group_by(Task.status)
            )
        ).all()
    )
    q = await moderation.quality(session, user_ids=[user.id])
    referrer = await session.get(User, user.referred_by_id) if user.referred_by_id else None
    memberships = [
        {"group": g.title, "purpose": g.purpose, "status": m.status if m else "unknown"}
        for g, m in (
            await session.execute(
                select(TgGroup, GroupMembership)
                .outerjoin(
                    GroupMembership,
                    (GroupMembership.group_id == TgGroup.id) & (GroupMembership.user_id == user.id),
                )
                .order_by(TgGroup.title)
            )
        ).all()
    ]
    days = await groups_svc.worked_days(session, [user.id])
    return {
        "tasks": {
            "total": sum(counts.values()),
            "active": counts.get(TaskStatus.ACTIVE, 0),
            "manual_review": counts.get(TaskStatus.MANUAL_REVIEW, 0),
            "moderation": counts.get(TaskStatus.MODERATION, 0),
            "approved": counts.get(TaskStatus.APPROVED, 0),
            "paid": counts.get(TaskStatus.PAID, 0),
            "rejected": counts.get(TaskStatus.REJECTED, 0),
        },
        "quality": q.as_dict(await settings_svc.get(session, "min_sample_units")),
        "training_status": user.training_status,
        "training": await candidates_svc.training_progress(session, user.id)
        if user.training_status != "none"
        else [],
        "newbie": groups_svc.progress_from_days(days.get(user.id, [])),
        "memberships": memberships,
        "referrer": referrer.full_name if referrer else None,
    }
