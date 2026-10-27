from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ActorDep, CurrentUser, SessionDep
from app.api.schemas import (
    MeOut,
    NameChangeOut,
    RegistrationIn,
    RegistrationOut,
    UserOut,
)
from app.models import AccountStatus, NameChangeRequest, RegistrationRequest, Role
from app.services import products as products_svc
from app.services import users as users_svc
from app.services.access import load_actor

router = APIRouter(tags=["me"])


def _screen(user) -> tuple[str, str | None]:
    if user.status in users_svc.STATUS_MESSAGES:
        return user.status.value, users_svc.STATUS_MESSAGES[user.status]
    if user.role == Role.OWNER and not user.first_name:
        return "register", None
    match user.status:
        case AccountStatus.UNREGISTERED:
            return "register", None
        case AccountStatus.PENDING:
            return "pending", "Заявка отправлена и ожидает рассмотрения"
        case AccountStatus.REJECTED:
            return "rejected", "Заявка отклонена"
    if not user.app_access:
        return "no_app_access", users_svc.NO_APP_ACCESS_MESSAGE
    return "ready", None


@router.get("/me", response_model=MeOut)
async def me(session: SessionDep, user: CurrentUser):
    reg = await session.scalar(
        select(RegistrationRequest)
        .where(RegistrationRequest.user_id == user.id)
        .order_by(RegistrationRequest.id.desc())
        .limit(1)
    )
    name_change = await session.scalar(
        select(NameChangeRequest)
        .where(NameChangeRequest.user_id == user.id)
        .order_by(NameChangeRequest.id.desc())
        .limit(1)
    )
    screen, message = _screen(user)
    actor = await load_actor(session, user)
    has_products = user.role == Role.OWNER or bool(
        await products_svc.user_product_ids(session, user.id)
    )
    return MeOut(
        user=UserOut.model_validate(user),
        screen=screen,
        message=message,
        registration=RegistrationOut.model_validate(reg) if reg else None,
        name_change=NameChangeOut.model_validate(name_change) if name_change else None,
        is_owner=actor.is_owner,
        permissions=sorted(actor.grants) if screen == "ready" else [],
        has_product_access=has_products,
        product_access_message=None if has_products else users_svc.NO_PRODUCTS_MESSAGE,
    )


@router.post("/registration", response_model=RegistrationOut | None)
async def register(body: RegistrationIn, session: SessionDep, user: CurrentUser):
    req = await users_svc.submit_registration(session, user, body.first_name, body.last_name)
    return RegistrationOut.model_validate(req) if req else None


@router.post("/me/name-change", response_model=NameChangeOut | None)
async def name_change(body: RegistrationIn, session: SessionDep, actor: ActorDep):
    req = await users_svc.request_name_change(session, actor.user, body.first_name, body.last_name)
    if isinstance(req, NameChangeRequest):
        return NameChangeOut.model_validate(req)
    return None


@router.delete("/me/name-change", status_code=204)
async def cancel_name_change(session: SessionDep, actor: ActorDep):
    await users_svc.cancel_name_change(session, actor.user)
