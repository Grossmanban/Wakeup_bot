from typing import Any

from fastapi import APIRouter

from app.api.deps import ActorDep, SessionDep
from app.services import settings as svc

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def get_settings(session: SessionDep, actor: ActorDep):
    actor.require_owner()
    return await svc.get_all(session)


@router.patch("/settings")
async def patch_settings(body: dict[str, Any], session: SessionDep, actor: ActorDep):
    return await svc.update(session, actor, body)
