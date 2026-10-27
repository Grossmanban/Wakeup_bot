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
from app.errors import Forbidden
from app.models import (
    AccountStatus,
    CandidateStatus,
    NameChangeRequest,
    RegistrationRequest,
    Role,
)
from app.services import candidates as candidates_svc
from app.services import entry as entry_svc
from app.services import products as products_svc
from app.services import users as users_svc
from app.services.access import load_actor

router = APIRouter(tags=["me"])


async def _screen(session, user) -> tuple[str, str | None]:
    if user.status in users_svc.STATUS_MESSAGES:
        return user.status.value, users_svc.STATUS_MESSAGES[user.status]
    if user.role == Role.OWNER and not user.first_name:
        return "register", None  # владелец один раз указывает ФИО
    if user.status in (AccountStatus.UNREGISTERED, AccountStatus.REJECTED):
        # Без приглашения или реферальной ссылки регистрация закрыта (скрытый режим).
        match await entry_svc.app_screen(session, user):
            case "staff_register":
                return "staff_register", None
            case "candidate":
                candidate = await candidates_svc.my_candidate(session, user)
                if candidate is not None and candidate.status not in (
                    CandidateStatus.DRAFT,
                    CandidateStatus.REJECTED,
                ):
                    return "candidate", None
                return "candidate_form", None
        if user.status == AccountStatus.REJECTED:
            return "rejected", "Заявка отклонена"
        await entry_svc.note_app_open(session, user)
        return "no_access", "Доступ по приглашению"
    if user.status == AccountStatus.PENDING:
        return "pending", "Заявка отправлена и ожидает рассмотрения"
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
    screen, message = await _screen(session, user)
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
        training_status=user.training_status,
    )


@router.post("/registration", response_model=RegistrationOut | None)
async def register(body: RegistrationIn, session: SessionDep, user: CurrentUser):
    """ФИО владельца при первом входе. Сотрудники регистрируются только по приглашению
    (POST /staff-registration), кандидаты — через анкету по реферальной ссылке."""
    if user.role != Role.OWNER:
        raise Forbidden("invite_required", "Регистрация только по приглашению")
    req = await users_svc.submit_registration(session, user, body.first_name, body.last_name)
    return RegistrationOut.model_validate(req) if req else None


@router.post("/staff-registration", response_model=UserOut)
async def staff_registration(body: RegistrationIn, session: SessionDep, user: CurrentUser):
    """Действующий сотрудник по приглашению: только фамилия и имя, сразу в работу."""
    user = await entry_svc.register_staff(session, user, body.first_name, body.last_name)
    return UserOut.model_validate(user)


@router.post("/me/name-change", response_model=NameChangeOut | None)
async def name_change(body: RegistrationIn, session: SessionDep, actor: ActorDep):
    req = await users_svc.request_name_change(session, actor.user, body.first_name, body.last_name)
    if isinstance(req, NameChangeRequest):
        return NameChangeOut.model_validate(req)
    return None


@router.delete("/me/name-change", status_code=204)
async def cancel_name_change(session: SessionDep, actor: ActorDep):
    await users_svc.cancel_name_change(session, actor.user)
