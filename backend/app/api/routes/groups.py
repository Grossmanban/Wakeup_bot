from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.errors import NotFound
from app.models import GroupInvite, TgGroup
from app.permissions import Perm
from app.services import groups as svc

router = APIRouter(tags=["groups"])


@router.get("/groups")
async def list_groups(session: SessionDep, actor: ActorDep):
    if not (actor.is_owner or actor.has(Perm.GROUPS_VIEW) or actor.has(Perm.GROUPS_INVITE)):
        actor.require(Perm.GROUPS_VIEW)
    rows = (await session.execute(select(TgGroup).order_by(TgGroup.title))).scalars()
    return [svc.group_out(g) for g in rows]


class AddGroupIn(BaseModel):
    chat_id: int


@router.post("/groups", status_code=201)
async def add_group(body: AddGroupIn, session: SessionDep, actor: ActorDep):
    return svc.group_out(await svc.add_by_chat_id(session, actor, body.chat_id))


class GroupPatch(BaseModel):
    purpose: Literal["training", "main", "photoreports", "summary", "audit", "other"] | None = None
    invite_cooldown_hours: int | None = Field(default=None, ge=0, le=720)


@router.patch("/groups/{group_id}")
async def update_group(group_id: int, body: GroupPatch, session: SessionDep, actor: ActorDep):
    return svc.group_out(
        await svc.update_group(session, actor, group_id, body.model_dump(exclude_unset=True))
    )


@router.post("/groups/{group_id}/check-rights")
async def check_rights(group_id: int, session: SessionDep, actor: ActorDep):
    return svc.group_out(await svc.check_rights(session, actor, group_id))


@router.post("/groups/{group_id}/test")
async def test_message(group_id: int, session: SessionDep, actor: ActorDep):
    return await svc.send_test(session, actor, group_id)


class CheckIn(BaseModel):
    user_ids: list[int] | None = Field(default=None, max_length=2000)


@router.post("/groups/{group_id}/check-members")
async def check_members(group_id: int, body: CheckIn, session: SessionDep, actor: ActorDep):
    return await svc.check_membership(session, actor, group_id, body.user_ids)


@router.get("/groups/overview")
async def overview(session: SessionDep, actor: ActorDep):
    return await svc.overview(session, actor)


class InviteIn(BaseModel):
    user_id: int


@router.post("/groups/{group_id}/invite")
async def invite(group_id: int, body: InviteIn, session: SessionDep, actor: ActorDep):
    return svc.invite_out(await svc.invite(session, actor, group_id, body.user_id))


class BulkIn(BaseModel):
    user_ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/groups/{group_id}/invite-bulk")
async def invite_bulk(group_id: int, body: BulkIn, session: SessionDep, actor: ActorDep):
    return await svc.invite_bulk(session, actor, group_id, body.user_ids)


@router.get("/groups/invites/{invite_id}")
async def get_invite(invite_id: int, session: SessionDep, actor: ActorDep):
    actor.require(Perm.GROUPS_INVITE)
    inv = await session.get(GroupInvite, invite_id)
    if inv is None:
        raise NotFound("Приглашение не найдено")
    return svc.invite_out(inv)
