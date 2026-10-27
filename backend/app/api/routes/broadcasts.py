from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import storage
from app.api.deps import ActorDep, SessionDep
from app.models import AuditEvent, User
from app.permissions import Perm
from app.services import audit_archive
from app.services import broadcasts as svc
from app.services.access import ALL, scope_for

router = APIRouter(tags=["broadcasts"])


class Audience(BaseModel):
    all: bool = False
    user_ids: list[int] = Field(default_factory=list, max_length=5000)
    network_ids: list[int] = Field(default_factory=list, max_length=100)
    coach_id: int | None = None
    instructor_id: int | None = None
    working_today: bool | None = None
    scheduled_tomorrow: bool | None = None
    no_status_tomorrow: bool = False


class DraftIn(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    audience: Audience
    require_ack: bool = False
    allow_replies: bool = False


@router.get("/broadcasts")
async def list_broadcasts(session: SessionDep, actor: ActorDep):
    return await svc.list_mine(session, actor)


@router.post("/broadcasts", status_code=201)
async def create(body: DraftIn, session: SessionDep, actor: ActorDep):
    return svc.out(await svc.save_draft(session, actor, None, body.model_dump()))


@router.put("/broadcasts/{bid}")
async def update(bid: int, body: DraftIn, session: SessionDep, actor: ActorDep):
    return svc.out(await svc.save_draft(session, actor, bid, body.model_dump()))


@router.post("/broadcasts/{bid}/photo")
async def photo(
    bid: int, session: SessionDep, actor: ActorDep, file: Annotated[UploadFile, File()]
):
    data = await file.read(storage.MAX_PHOTO_BYTES + 1)
    return svc.out(await svc.set_photo(session, actor, bid, data))


@router.delete("/broadcasts/{bid}/photo")
async def drop_photo(bid: int, session: SessionDep, actor: ActorDep):
    return svc.out(await svc.set_photo(session, actor, bid, None))


@router.get("/broadcasts/{bid}/preview")
async def preview(bid: int, session: SessionDep, actor: ActorDep):
    return await svc.preview(session, actor, bid)


class SendIn(BaseModel):
    expected_count: int = Field(ge=0)


@router.post("/broadcasts/{bid}/send")
async def send(bid: int, body: SendIn, session: SessionDep, actor: ActorDep):
    return svc.out(await svc.send(session, actor, bid, body.expected_count))


@router.get("/broadcasts/{bid}")
async def details(bid: int, session: SessionDep, actor: ActorDep):
    return await svc.details(session, actor, bid)


@router.get("/my/broadcasts")
async def received(session: SessionDep, actor: ActorDep):
    return await svc.received(session, actor.user)


@router.post("/my/broadcasts/{bid}/ack")
async def ack(bid: int, session: SessionDep, actor: ActorDep):
    return {"ok": await svc.acknowledge(session, actor.user, bid)}


# --- журнал аудита ------------------------------------------------------------------


@router.get("/audit")
async def audit_log(
    session: SessionDep,
    actor: ActorDep,
    category: str | None = None,
    user_id: int | None = None,
    before_id: int | None = None,
    since: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    actor.require(Perm.AUDIT_VIEW)
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    scope = await scope_for(session, actor, Perm.AUDIT_VIEW)
    if scope is not ALL:
        # С ограниченной областью видны только события по своим людям.
        q = q.where(AuditEvent.target_user_id.in_(scope))
    if category:
        q = q.where(AuditEvent.category == category)
    if user_id:
        q = q.where((AuditEvent.target_user_id == user_id) | (AuditEvent.actor_id == user_id))
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    if since:
        q = q.where(AuditEvent.occurred_at >= since)
    events = (await session.execute(q)).scalars().all()
    ids = {i for e in events for i in (e.actor_id, e.target_user_id) if i}
    names = {
        u.id: u.full_name
        for u in (await session.execute(select(User).where(User.id.in_(ids or {0})))).scalars()
    }
    return [
        {
            "id": e.id,
            "occurred_at": e.occurred_at,
            "category": e.category,
            "action": e.action,
            "actor": names.get(e.actor_id) if e.actor_id else "система",
            "target": names.get(e.target_user_id),
            "object_type": e.object_type,
            "object_id": e.object_id,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "reason": e.reason,
            "result": e.result,
        }
        for e in events
    ]


@router.post("/groups/{group_id}/audit-topics")
async def audit_topics(group_id: int, session: SessionDep, actor: ActorDep):
    g = await audit_archive.setup_topics(session, actor, group_id)
    return {"topics": g.topics, "error": g.check_error}
