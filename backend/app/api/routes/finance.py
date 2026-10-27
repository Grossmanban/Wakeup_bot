from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.api.schemas_products import ReasonIn
from app.errors import NotFound
from app.models import BonusRule, EntryKind, LazyExemption, LazyRule, Payout
from app.permissions import Perm
from app.services import finance as svc
from app.services.access import get_target

router = APIRouter(tags=["finance"])


class PenaltyIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    units: int | None = Field(default=None, ge=1, le=100000)
    product_id: int | None = None
    amount_kop: int | None = Field(default=None, ge=1, le=10**10)
    occurred_on: date | None = None
    request_key: str = Field(min_length=8, max_length=64)


class BonusIn(BaseModel):
    amount_kop: int = Field(ge=1, le=10**10)
    reason: str = Field(min_length=1, max_length=1000)
    occurred_on: date | None = None
    request_key: str = Field(min_length=8, max_length=64)


class AdjustmentIn(BaseModel):
    amount_kop: int = Field(ge=-(10**10), le=10**10)
    reason: str = Field(min_length=1, max_length=1000)
    kind: Literal["adjustment", "offset"] = "adjustment"
    source_entry_id: int | None = None
    request_key: str = Field(min_length=8, max_length=64)


def entry_out(e) -> dict:
    return {
        "id": e.id,
        "kind": e.kind.value,
        "amount_kop": e.amount_kop,
        "units": e.units,
        "rate_kop": e.rate_kop,
    }


@router.get("/my/finance")
async def my_finance(
    session: SessionDep, actor: ActorDep, start: date | None = None, end: date | None = None
):
    return await svc.overview(session, actor.id, start, end)


@router.get("/users/{user_id}/finance")
async def user_finance(
    user_id: int,
    session: SessionDep,
    actor: ActorDep,
    start: date | None = None,
    end: date | None = None,
):
    # Смотреть финансы может тот, у кого есть любое из финансовых прав на этого человека.
    for perm in (Perm.FINANCE_VIEW, Perm.FINANCE_MANAGE, Perm.PAYOUTS_MANAGE):
        if actor.has(perm):
            await get_target(session, actor, perm, user_id)
            return await svc.overview(session, user_id, start, end)
    await get_target(session, actor, Perm.FINANCE_VIEW, user_id)


@router.post("/users/{user_id}/penalties", status_code=201)
async def penalty(user_id: int, body: PenaltyIn, session: SessionDep, actor: ActorDep):
    e = await svc.add_penalty(
        session,
        actor,
        user_id,
        reason=body.reason,
        units=body.units,
        product_id=body.product_id,
        amount_kop=body.amount_kop,
        occurred_on=body.occurred_on,
        request_key=body.request_key,
    )
    return entry_out(e)


@router.post("/users/{user_id}/bonuses", status_code=201)
async def bonus(user_id: int, body: BonusIn, session: SessionDep, actor: ActorDep):
    e = await svc.add_bonus(
        session,
        actor,
        user_id,
        amount_kop=body.amount_kop,
        reason=body.reason,
        occurred_on=body.occurred_on,
        request_key=body.request_key,
    )
    return entry_out(e)


@router.post("/users/{user_id}/adjustments", status_code=201)
async def adjustment(user_id: int, body: AdjustmentIn, session: SessionDep, actor: ActorDep):
    e = await svc.add_adjustment(
        session,
        actor,
        user_id,
        amount_kop=body.amount_kop,
        reason=body.reason,
        kind=EntryKind(body.kind),
        source_entry_id=body.source_entry_id,
        request_key=body.request_key,
    )
    return entry_out(e)


@router.post("/ledger/{entry_id}/cancel-bonus", status_code=201)
async def cancel_bonus(entry_id: int, body: ReasonIn, session: SessionDep, actor: ActorDep):
    return entry_out(await svc.cancel_bonus(session, actor, entry_id, body.reason or ""))


# --- выплаты ------------------------------------------------------------------------


@router.get("/users/{user_id}/payout-preview")
async def payout_preview(
    user_id: int, start: date, end: date, session: SessionDep, actor: ActorDep
):
    return await svc.preview(session, actor, user_id, start, end)


class PayoutIn(BaseModel):
    start: date
    end: date
    idempotency_key: str = Field(min_length=8, max_length=64)
    expected_total_kop: int | None = None
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/users/{user_id}/payouts", status_code=201)
async def payout_confirm(user_id: int, body: PayoutIn, session: SessionDep, actor: ActorDep):
    p = await svc.confirm(
        session,
        actor,
        user_id,
        body.start,
        body.end,
        idempotency_key=body.idempotency_key,
        expected_total_kop=body.expected_total_kop,
        comment=body.comment,
    )
    return {"id": p.id, "amount_kop": p.amount_kop, "snapshot": p.snapshot}


@router.get("/payouts/{payout_id}")
async def get_payout(payout_id: int, session: SessionDep, actor: ActorDep):
    p = await session.get(Payout, payout_id)
    if p is None:
        raise NotFound("Выплата не найдена")
    if p.user_id != actor.id:
        await get_target(session, actor, Perm.FINANCE_VIEW, p.user_id)
    return {
        "id": p.id,
        "user_id": p.user_id,
        "amount_kop": p.amount_kop,
        "snapshot": p.snapshot,
        "created_at": p.created_at,
        "comment": p.comment,
    }


# --- правила ------------------------------------------------------------------------


class Condition(BaseModel):
    product_id: int
    min_units: int = Field(ge=1, le=100000)


class BonusRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    amount_kop: int = Field(ge=1, le=10**10)
    conditions: list[Condition] = Field(min_length=1, max_length=20)
    min_quality: int | None = Field(default=None, ge=0, le=100)
    period_from: date
    period_to: date
    is_active: bool = True


def rule_out(r: BonusRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "amount_kop": r.amount_kop,
        "conditions": r.conditions,
        "min_quality": r.min_quality,
        "period_from": r.period_from,
        "period_to": r.period_to,
        "is_active": r.is_active,
    }


@router.get("/bonus-rules")
async def list_bonus_rules(session: SessionDep, actor: ActorDep):
    actor.require(Perm.FINANCE_MANAGE)
    return [
        rule_out(r)
        for r in (await session.execute(select(BonusRule).order_by(BonusRule.id.desc()))).scalars()
    ]


@router.post("/bonus-rules", status_code=201)
async def create_bonus_rule(body: BonusRuleIn, session: SessionDep, actor: ActorDep):
    data = body.model_dump()
    return rule_out(await svc.save_bonus_rule(session, actor, None, data))


@router.put("/bonus-rules/{rule_id}")
async def update_bonus_rule(rule_id: int, body: BonusRuleIn, session: SessionDep, actor: ActorDep):
    return rule_out(await svc.save_bonus_rule(session, actor, rule_id, body.model_dump()))


class LazyRuleIn(BaseModel):
    product_id: int
    min_daily_units: int = Field(ge=1, le=100000)
    reduced_rate_kop: int = Field(ge=0, le=10**10)
    roles: list[Literal["admin", "coach", "instructor", "employee"]] = Field(default_factory=list)
    user_ids: list[int] = Field(default_factory=list, max_length=1000)
    period_from: date
    period_to: date | None = None
    is_active: bool = True


def lazy_out(r: LazyRule, exempt: list[int]) -> dict:
    return {
        "id": r.id,
        "product_id": r.product_id,
        "min_daily_units": r.min_daily_units,
        "reduced_rate_kop": r.reduced_rate_kop,
        "roles": r.roles,
        "user_ids": r.user_ids,
        "period_from": r.period_from,
        "period_to": r.period_to,
        "is_active": r.is_active,
        "exempt_user_ids": exempt,
    }


@router.get("/lazy-rules")
async def list_lazy_rules(session: SessionDep, actor: ActorDep):
    actor.require(Perm.FINANCE_MANAGE)
    rules = (await session.execute(select(LazyRule).order_by(LazyRule.id.desc()))).scalars().all()
    ex: dict[int, list[int]] = {}
    for rid, uid in await session.execute(select(LazyExemption.rule_id, LazyExemption.user_id)):
        ex.setdefault(rid, []).append(uid)
    return [lazy_out(r, ex.get(r.id, [])) for r in rules]


@router.post("/lazy-rules", status_code=201)
async def create_lazy_rule(body: LazyRuleIn, session: SessionDep, actor: ActorDep):
    return lazy_out(await svc.save_lazy_rule(session, actor, None, body.model_dump()), [])


@router.put("/lazy-rules/{rule_id}")
async def update_lazy_rule(rule_id: int, body: LazyRuleIn, session: SessionDep, actor: ActorDep):
    return lazy_out(await svc.save_lazy_rule(session, actor, rule_id, body.model_dump()), [])


class ExemptionIn(BaseModel):
    user_id: int
    on: bool


@router.post("/lazy-rules/{rule_id}/exemptions", status_code=204)
async def lazy_exemption(rule_id: int, body: ExemptionIn, session: SessionDep, actor: ActorDep):
    await svc.set_lazy_exemption(session, actor, rule_id, body.user_id, body.on)


@router.get("/finance/unpaid")
async def unpaid(session: SessionDep, actor: ActorDep):
    """Сотрудники с невыплаченными начислениями или долгом — для раздела «Выплаты»."""
    return await svc.unpaid_list(session, actor)
