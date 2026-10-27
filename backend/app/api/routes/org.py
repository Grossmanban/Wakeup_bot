from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.api.schemas import NetworkIn, NetworkOut, NetworkPatch, SupervisionIn, UserNetworksIn
from app.models import Network
from app.permissions import Perm
from app.services import org as org_svc

router = APIRouter(tags=["org"])


@router.get("/networks", response_model=list[NetworkOut])
async def list_networks(session: SessionDep, actor: ActorDep, include_archived: bool = False):
    # Список названий сетей нужен для фильтров всем, кто видит сотрудников.
    q = select(Network).order_by(Network.name)
    if not include_archived:
        q = q.where(Network.is_archived.is_(False))
    if not (actor.has_global(Perm.ORG_MANAGE) or actor.has_global(Perm.USERS_VIEW)):
        ids = set()
        for networks in actor.grants.values():
            ids |= {n for n in networks if n is not None}
        q = q.where(Network.id.in_(ids))
    return list((await session.execute(q)).scalars())


@router.post("/networks", response_model=NetworkOut, status_code=201)
async def create_network(body: NetworkIn, session: SessionDep, actor: ActorDep):
    return await org_svc.create_network(session, actor, body.name)


@router.patch("/networks/{network_id}", response_model=NetworkOut)
async def update_network(network_id: int, body: NetworkPatch, session: SessionDep, actor: ActorDep):
    return await org_svc.update_network(
        session, actor, network_id, name=body.name, archived=body.is_archived
    )


@router.put("/users/{user_id}/networks", response_model=list[int])
async def set_user_networks(
    user_id: int, body: UserNetworksIn, session: SessionDep, actor: ActorDep
):
    return sorted(await org_svc.set_user_networks(session, actor, user_id, set(body.network_ids)))


@router.post("/supervisions", status_code=204)
async def add_supervision(body: SupervisionIn, session: SessionDep, actor: ActorDep):
    await org_svc.add_supervision(session, actor, body.supervisor_id, body.subordinate_id)


@router.delete("/supervisions", status_code=204)
async def remove_supervision(body: SupervisionIn, session: SessionDep, actor: ActorDep):
    await org_svc.remove_supervision(session, actor, body.supervisor_id, body.subordinate_id)
