"""Скрытый режим: приглашения действующих сотрудников и журнал попыток доступа."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, SessionDep
from app.services import entry as svc

router = APIRouter(tags=["access"])


class InviteIn(BaseModel):
    # None — без срока действия.
    expires_in_hours: int | None = Field(72, ge=1, le=svc.MAX_INVITE_DAYS * 24)
    max_uses: int = Field(1, ge=1, le=50)
    note: str | None = Field(None, max_length=200)


@router.get("/staff-invites")
async def list_invites(session: SessionDep, actor: ActorDep):
    return await svc.list_invites(session, actor)


@router.post("/staff-invites", status_code=201)
async def create_invite(body: InviteIn, session: SessionDep, actor: ActorDep):
    inv = await svc.create_invite(
        session,
        actor,
        expires_in_hours=body.expires_in_hours,
        max_uses=body.max_uses,
        note=body.note,
    )
    return svc.invite_out(inv, [], actor.user.full_name, actor)


@router.post("/staff-invites/{invite_id}/revoke")
async def revoke_invite(invite_id: int, session: SessionDep, actor: ActorDep):
    inv = await svc.revoke_invite(session, actor, invite_id)
    return svc.invite_out(inv, [], None, actor)


@router.get("/access-attempts")
async def attempts(
    session: SessionDep, actor: ActorDep, status: str | None = None, q: str | None = None
):
    return await svc.list_attempts(session, actor, status, q)


@router.get("/access-attempts/{telegram_id}")
async def attempt(telegram_id: int, session: SessionDep, actor: ActorDep):
    return await svc.attempt_card(session, actor, telegram_id)
