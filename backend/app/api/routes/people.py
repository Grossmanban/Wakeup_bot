from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ActorDep, CurrentUser, SessionDep
from app.api.schemas_products import ReasonIn
from app.errors import NotFound
from app.models import GoalStatus, TrainingSession
from app.services import candidates as cand
from app.services import goals as goals_svc
from app.services import settings as settings_svc
from app.timeutil import MSK

router = APIRouter(tags=["people"])


# --- цели ---------------------------------------------------------------------------


class Spec(BaseModel):
    type: Literal["user", "users", "network", "coach_team", "instructor_team", "custom"]
    ids: list[int] = Field(max_length=2000)


class Prize(BaseModel):
    place: int = Field(ge=1, le=100)
    prize: str = Field(min_length=1, max_length=200)


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["goal", "competition"]
    scope: Literal["individual", "team"] = "individual"
    participants_spec: Spec
    product_ids: list[int] = Field(min_length=1, max_length=100)
    target_units: int | None = Field(default=None, ge=1, le=10**7)
    period_from: date
    period_to: date
    deadline_type: Literal["period", "deadline"] = "period"
    visibility: Literal["participants", "all", "managers"] = "participants"
    counting: Literal["completed", "approved", "paid"] = "approved"
    freeze_participants: bool = True
    mode: Literal["race", "max"] | None = None
    ranking: Literal["individual", "total", "average", "min_each"] = "individual"
    prizes: list[Prize] = Field(default_factory=list, max_length=50)
    exclusions: list[int] = Field(default_factory=list, max_length=1000)
    tie_break: Literal["earliest", "shared"] = "earliest"


@router.get("/goals")
async def list_goals(session: SessionDep, actor: ActorDep):
    return await goals_svc.visible_goals(session, actor)


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: int, session: SessionDep, actor: ActorDep):
    return await goals_svc.goal_details(session, actor, goal_id)


@router.post("/goals", status_code=201)
async def create_goal(body: GoalIn, session: SessionDep, actor: ActorDep):
    g = await goals_svc.save_goal(session, actor, None, body.model_dump())
    return goals_svc.goal_out(g)


@router.put("/goals/{goal_id}")
async def update_goal(goal_id: int, body: GoalIn, session: SessionDep, actor: ActorDep):
    return goals_svc.goal_out(await goals_svc.save_goal(session, actor, goal_id, body.model_dump()))


class GoalStatusIn(BaseModel):
    status: GoalStatus


@router.put("/goals/{goal_id}/status")
async def goal_status(goal_id: int, body: GoalStatusIn, session: SessionDep, actor: ActorDep):
    return goals_svc.goal_out(await goals_svc.set_status(session, actor, goal_id, body.status))


# --- рефералы -----------------------------------------------------------------------


@router.get("/my/referral")
async def my_referral(session: SessionDep, actor: ActorDep):
    link = await cand.referral_link(session, actor)
    return {**link, **(await cand.referral_stats(session, actor))}


@router.get("/my/referral/qr.svg")
async def my_referral_qr(session: SessionDep, actor: ActorDep):
    link = await cand.referral_link(session, actor)
    return Response(cand.qr_svg(link["link"]), media_type="image/svg+xml")


# --- анкета кандидата ---------------------------------------------------------------


class CandidateForm(BaseModel):
    first_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    days_per_week: int | None = Field(default=None, ge=1, le=7)
    can_train: bool | None = None
    skills: dict[str, int] | None = None
    truthful: bool | None = None
    pd_consent: bool | None = None


async def _candidate_view(session, user) -> dict:
    c = await cand.my_candidate(session, user)
    trainings = []
    if c is not None:
        trainings = [
            cand.training_out(t)
            for t in (
                await session.execute(
                    select(TrainingSession)
                    .where(TrainingSession.candidate_id == c.id)
                    .order_by(TrainingSession.id)
                )
            ).scalars()
        ]
    return {
        "candidate": cand.candidate_out(c) if c else None,
        "trainings": trainings,
        "skills": await settings_svc.get(session, "candidate_skills"),
        "pd_consent_version": await settings_svc.get(session, "pd_consent_version"),
        "pd_consent_url": await settings_svc.get(session, "pd_consent_url"),
        "referred": user.referred_by_id is not None,
    }


@router.get("/my/candidate")
async def my_candidate(session: SessionDep, user: CurrentUser):
    return await _candidate_view(session, user)


@router.put("/my/candidate")
async def save_candidate(body: CandidateForm, session: SessionDep, user: CurrentUser):
    await cand.save_form(session, user, body.model_dump(exclude_none=True))
    return await _candidate_view(session, user)


@router.post("/my/candidate/submit")
async def submit_candidate(session: SessionDep, user: CurrentUser):
    await cand.submit(session, user)
    return await _candidate_view(session, user)


class ContactIn(BaseModel):
    status: Literal["wrote", "cannot_contact"]


@router.post("/my/candidate/contact")
async def candidate_contact(body: ContactIn, session: SessionDep, user: CurrentUser):
    await cand.report_contact(session, user, body.status)
    return await _candidate_view(session, user)


@router.post("/my/trainings/{training_id}/confirm")
async def confirm_training(training_id: int, session: SessionDep, user: CurrentUser):
    await cand.confirm_training(session, user, training_id)
    return await _candidate_view(session, user)


# --- рассмотрение кандидатов --------------------------------------------------------


@router.get("/candidates")
async def candidates(session: SessionDep, actor: ActorDep, status: str | None = None):
    return await cand.list_candidates(session, actor, status)


@router.get("/candidates/{cid}")
async def candidate(cid: int, session: SessionDep, actor: ActorDep):
    return await cand.candidate_details(session, actor, cid)


class ReviewIn(BaseModel):
    text: str | None = Field(default=None, max_length=2000)


@router.post("/candidates/{cid}/review/{action}")
async def review(
    cid: int,
    action: Literal["take", "approve", "reject", "request_info", "comment"],
    body: ReviewIn,
    session: SessionDep,
    actor: ActorDep,
):
    c = await cand.review_action(session, actor, cid, action, body.text)
    return cand.candidate_out(c)


class ResponsibleIn(BaseModel):
    responsible_id: int
    send_template: bool = True


@router.put("/candidates/{cid}/responsible")
async def responsible(cid: int, body: ResponsibleIn, session: SessionDep, actor: ActorDep):
    return await cand.assign_responsible(
        session, actor, cid, body.responsible_id, body.send_template
    )


class TrainingIn(BaseModel):
    starts_at: datetime
    place: str = Field(min_length=1, max_length=300)
    contact_user_id: int | None = None
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/candidates/{cid}/trainings", status_code=201)
async def schedule_training(cid: int, body: TrainingIn, session: SessionDep, actor: ActorDep):
    if body.starts_at.tzinfo is None:
        body.starts_at = body.starts_at.replace(tzinfo=MSK)
    t = await cand.schedule_training(
        session,
        actor,
        cid,
        starts_at=body.starts_at,
        place=body.place,
        contact_user_id=body.contact_user_id,
        comment=body.comment,
    )
    return cand.training_out(t)


class CancelTrainingIn(ReasonIn):
    force: bool = False


@router.post("/trainings/{training_id}/cancel")
async def cancel_training(
    training_id: int, body: CancelTrainingIn, session: SessionDep, actor: ActorDep
):
    return cand.training_out(
        await cand.cancel_training(session, actor, training_id, body.reason, body.force)
    )


@router.post("/candidates/{cid}/start-training")
async def start_training(cid: int, session: SessionDep, actor: ActorDep):
    u = await cand.start_training(session, actor, cid)
    return {"user_id": u.id, "status": u.status.value, "training_status": u.training_status}


@router.get("/trainees")
async def trainees(session: SessionDep, actor: ActorDep):
    return await cand.trainees(session, actor)


class ConfirmUnitsIn(BaseModel):
    product_id: int
    confirmed_units: int = Field(ge=0, le=100000)
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/trainees/{user_id}/confirm-units")
async def confirm_units(user_id: int, body: ConfirmUnitsIn, session: SessionDep, actor: ActorDep):
    return await cand.confirm_units(
        session, actor, user_id, body.product_id, body.confirmed_units, body.comment
    )


@router.get("/my/training")
async def my_training(session: SessionDep, actor: ActorDep):
    if actor.user.training_status == "none":
        raise NotFound("Вы не проходите обучение")
    return {
        "training_status": actor.user.training_status,
        "progress": await cand.training_progress(session, actor.id),
    }
