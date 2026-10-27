from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import timeutil
from app.api.deps import ActorDep, SessionDep
from app.services import photoreports as svc

router = APIRouter(tags=["photoreports"])


class RouteIn(BaseModel):
    kind: Literal["product", "general", "summary", "extra"]
    product_id: int | None = None
    group_id: int
    is_active: bool = True


@router.get("/photoreports")
async def overview(session: SessionDep, actor: ActorDep, day: date | None = None):
    return await svc.overview(session, actor, day or timeutil.msk_today())


@router.post("/photoreports/routes", status_code=201)
async def save_route(body: RouteIn, session: SessionDep, actor: ActorDep):
    r = await svc.save_route(
        session,
        actor,
        kind=body.kind,
        product_id=body.product_id,
        group_id=body.group_id,
        is_active=body.is_active,
    )
    return svc.route_out(r)


@router.delete("/photoreports/routes/{route_id}", status_code=204)
async def delete_route(route_id: int, session: SessionDep, actor: ActorDep):
    await svc.delete_route(session, actor, route_id)


@router.post("/photoreports/deliveries/{delivery_id}/retry")
async def retry(delivery_id: int, session: SessionDep, actor: ActorDep):
    d = await svc.retry_delivery(session, actor, delivery_id)
    return {"id": d.id, "status": d.status}
