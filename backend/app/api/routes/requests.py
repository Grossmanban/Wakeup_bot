from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.api.schemas import (
    DecisionIn,
    NameChangeOut,
    NameChangeWithUser,
    RegistrationOut,
    RegistrationWithUser,
    UserShort,
)
from app.models import NameChangeRequest, RegistrationRequest, RequestStatus, User
from app.permissions import Perm
from app.services import users as users_svc
from app.services.access import ALL, scope_for

router = APIRouter(tags=["requests"])


@router.get("/registrations", response_model=list[RegistrationWithUser])
async def list_registrations(
    session: SessionDep, actor: ActorDep, status: RequestStatus = RequestStatus.PENDING
):
    actor.require(Perm.REGISTRATIONS_REVIEW)
    rows = await session.execute(
        select(RegistrationRequest, User)
        .join(User, User.id == RegistrationRequest.user_id)
        .where(RegistrationRequest.status == status)
        .order_by(RegistrationRequest.created_at)
        .limit(500)
    )
    return [
        RegistrationWithUser(
            **RegistrationOut.model_validate(r).model_dump(), user=UserShort.model_validate(u)
        )
        for r, u in rows
    ]


@router.post("/registrations/{request_id}/approve", response_model=RegistrationOut)
async def approve_registration(
    request_id: int, session: SessionDep, actor: ActorDep, body: DecisionIn | None = None
):
    return await users_svc.decide_registration(
        session, actor, request_id, approve=True, reason=body.reason if body else None
    )


@router.post("/registrations/{request_id}/reject", response_model=RegistrationOut)
async def reject_registration(
    request_id: int, session: SessionDep, actor: ActorDep, body: DecisionIn | None = None
):
    return await users_svc.decide_registration(
        session, actor, request_id, approve=False, reason=body.reason if body else None
    )


@router.get("/name-changes", response_model=list[NameChangeWithUser])
async def list_name_changes(
    session: SessionDep, actor: ActorDep, status: RequestStatus = RequestStatus.PENDING
):
    actor.require(Perm.NAME_CHANGES_REVIEW)
    scope = await scope_for(session, actor, Perm.NAME_CHANGES_REVIEW)
    q = (
        select(NameChangeRequest, User)
        .join(User, User.id == NameChangeRequest.user_id)
        .where(NameChangeRequest.status == status)
        .order_by(NameChangeRequest.created_at)
        .limit(500)
    )
    if scope is not ALL:
        q = q.where(User.id.in_(scope))
    rows = await session.execute(q)
    return [
        NameChangeWithUser(
            **NameChangeOut.model_validate(r).model_dump(), user=UserShort.model_validate(u)
        )
        for r, u in rows
    ]


@router.post("/name-changes/{request_id}/approve", response_model=NameChangeOut)
async def approve_name_change(
    request_id: int, session: SessionDep, actor: ActorDep, body: DecisionIn | None = None
):
    return await users_svc.decide_name_change(
        session, actor, request_id, approve=True, reason=body.reason if body else None
    )


@router.post("/name-changes/{request_id}/reject", response_model=NameChangeOut)
async def reject_name_change(
    request_id: int, session: SessionDep, actor: ActorDep, body: DecisionIn | None = None
):
    return await users_svc.decide_name_change(
        session, actor, request_id, approve=False, reason=body.reason if body else None
    )
