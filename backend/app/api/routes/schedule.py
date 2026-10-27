from datetime import date, time
from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.api.schemas_products import ReasonIn
from app.models import WorkPoint
from app.services import idempotency
from app.services import schedule as svc

router = APIRouter(tags=["schedule"])


class PointIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    address: str | None = Field(default=None, max_length=500)
    capacity: int | None = Field(default=None, ge=1, le=10000)


class PointPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    address: str | None = Field(default=None, max_length=500)
    capacity: int | None = Field(default=None, ge=1, le=10000)
    is_archived: bool | None = None


def point_out(p: WorkPoint) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "address": p.address,
        "capacity": p.capacity,
        "is_archived": p.is_archived,
    }


@router.get("/work-points")
async def list_points(session: SessionDep, actor: ActorDep, include_archived: bool = False):
    q = select(WorkPoint).order_by(WorkPoint.name)
    if not include_archived:
        q = q.where(WorkPoint.is_archived.is_(False))
    return [point_out(p) for p in (await session.execute(q)).scalars()]


@router.post("/work-points", status_code=201)
async def create_point(body: PointIn, session: SessionDep, actor: ActorDep):
    return point_out(await svc.create_point(session, actor, body.name, body.address, body.capacity))


@router.patch("/work-points/{point_id}")
async def update_point(point_id: int, body: PointPatch, session: SessionDep, actor: ActorDep):
    changes = body.model_dump(exclude_unset=True)
    return point_out(await svc.update_point(session, actor, point_id, changes))


class Plan(BaseModel):
    product_id: int
    units: int = Field(ge=0, le=10000)


class ShiftIn(BaseModel):
    day: date
    start: time
    end: time
    point_id: int
    plans: list[Plan] = Field(default_factory=list, max_length=50)
    user_id: int | None = None  # для руководителя: поставить в график другого сотрудника


class ShiftPatch(BaseModel):
    start: time
    end: time
    point_id: int


class PlansIn(BaseModel):
    plans: list[Plan] = Field(max_length=50)


def _plans(items: list[Plan]) -> dict[int, int]:
    return {p.product_id: p.units for p in items}


@router.get("/my/schedule")
async def my_schedule(session: SessionDep, actor: ActorDep, start: date | None = None):
    return await svc.my_schedule(session, actor, start)


@router.post("/shifts", status_code=201)
async def create_shift(
    body: ShiftIn,
    session: SessionDep,
    actor: ActorDep,
    idem_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    if (saved := await idempotency.lookup(session, actor.id, idem_key, "shift.create")) is not None:
        return saved
    shift = await svc.create_shift(
        session,
        actor,
        user_id=body.user_id or actor.id,
        day=body.day,
        start=body.start,
        end=body.end,
        point_id=body.point_id,
        plans=_plans(body.plans),
    )
    result = {"id": shift.id, "status": shift.status.value}
    await idempotency.remember(session, actor.id, idem_key, "shift.create", result)
    await session.commit()
    return result


@router.patch("/shifts/{shift_id}")
async def update_shift(shift_id: int, body: ShiftPatch, session: SessionDep, actor: ActorDep):
    s = await svc.update_shift(
        session, actor, shift_id, start=body.start, end=body.end, point_id=body.point_id
    )
    return {"id": s.id, "status": s.status.value}


@router.put("/shifts/{shift_id}/plans")
async def set_plans(shift_id: int, body: PlansIn, session: SessionDep, actor: ActorDep):
    s = await svc.set_plans(session, actor, shift_id, _plans(body.plans))
    return {"id": s.id, "status": s.status.value}


@router.post("/shifts/{shift_id}/cancel")
async def cancel_shift(shift_id: int, body: ReasonIn, session: SessionDep, actor: ActorDep):
    s = await svc.cancel_shift(session, actor, shift_id, body.reason)
    return {"id": s.id, "status": s.status.value}


@router.post("/shifts/{shift_id}/confirm")
async def confirm_shift(shift_id: int, session: SessionDep, actor: ActorDep):
    s = await svc.decide_shift(session, actor, shift_id, True, None)
    return {"id": s.id, "status": s.status.value}


@router.post("/shifts/{shift_id}/reject")
async def reject_shift(shift_id: int, body: ReasonIn, session: SessionDep, actor: ActorDep):
    s = await svc.decide_shift(session, actor, shift_id, False, body.reason)
    return {"id": s.id, "status": s.status.value}


@router.get("/shifts/pending")
async def pending(session: SessionDep, actor: ActorDep):
    return await svc.pending_shifts(session, actor)


class DayOffIn(BaseModel):
    day: date
    on: bool
    user_id: int | None = None


@router.post("/day-off", status_code=204)
async def day_off(body: DayOffIn, session: SessionDep, actor: ActorDep):
    await svc.set_day_off(session, actor, body.user_id or actor.id, body.day, body.on)


@router.get("/schedule/summary")
async def summary(
    session: SessionDep,
    actor: ActorDep,
    start: date,
    end: date,
    network_id: int | None = None,
    coach_id: int | None = None,
    instructor_id: int | None = None,
    user_id: int | None = None,
    product_id: int | None = None,
):
    return await svc.summary(
        session,
        actor,
        start=start,
        end=end,
        network_id=network_id,
        coach_id=coach_id,
        instructor_id=instructor_id,
        user_id=user_id,
        product_id=product_id,
    )


@router.get("/schedule/distribution")
async def distribution(
    session: SessionDep,
    actor: ActorDep,
    day: date,
    network_id: int | None = None,
    coach_id: int | None = None,
    instructor_id: int | None = None,
    product_id: int | None = None,
):
    return await svc.distribution(
        session,
        actor,
        day=day,
        network_id=network_id,
        coach_id=coach_id,
        instructor_id=instructor_id,
        product_id=product_id,
    )


@router.get("/schedule/colleagues")
async def colleagues(session: SessionDep, actor: ActorDep, start: date, end: date):
    return await svc.colleagues(session, actor, start, end)
