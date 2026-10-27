"""Цели и соревнования (ТЗ 10). Результаты — только по системным записям, без двойного учёта."""

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import timeutil
from app.errors import AppError, Forbidden, NotFound
from app.models import (
    DONE_STATUSES,
    AccountStatus,
    Goal,
    GoalParticipant,
    GoalStatus,
    Product,
    Task,
    TaskStatus,
    User,
    UserNetwork,
)
from app.permissions import Perm
from app.services import audit
from app.services.access import ALL, Actor, scope_for, team_of
from app.services.audit import Category

SPEC_TYPES = ("user", "users", "network", "coach_team", "instructor_team", "custom")
FIELDS = (
    "title",
    "kind",
    "scope",
    "participants_spec",
    "product_ids",
    "target_units",
    "period_from",
    "period_to",
    "deadline_type",
    "visibility",
    "counting",
    "freeze_participants",
    "mode",
    "ranking",
    "prizes",
    "exclusions",
    "tie_break",
)


async def resolve_participants(session: AsyncSession, spec: dict[str, Any]) -> set[int]:
    """Участники по описанию группы. Пересечения групп не дают повторов — это множество."""
    kind, ids = spec.get("type"), [int(x) for x in spec.get("ids", [])]
    if kind not in SPEC_TYPES:
        raise AppError("bad_participants", "Неизвестный тип участников")
    people: set[int] = set()
    if kind in ("user", "users", "custom"):
        people = set(ids)
    elif kind == "network":
        people = set(
            (
                await session.execute(
                    select(UserNetwork.user_id).where(UserNetwork.network_id.in_(ids or [0]))
                )
            ).scalars()
        )
    else:
        for lead in ids:
            people |= await team_of(session, lead)
    active = set(
        (
            await session.execute(
                select(User.id).where(
                    User.id.in_(people or {0}), User.status == AccountStatus.ACTIVE
                )
            )
        ).scalars()
    )
    return active


def _validate(data: dict[str, Any]) -> None:
    if data["kind"] not in ("goal", "competition"):
        raise AppError("bad_goal", "Тип — цель или соревнование")
    if data["period_to"] < data["period_from"]:
        raise AppError("bad_period", "Конец периода раньше начала")
    if not data["product_ids"]:
        raise AppError("bad_goal", "Выберите продукты")
    if data["kind"] == "goal" and not data.get("target_units"):
        raise AppError("bad_goal", "Укажите объём цели")
    if data["kind"] == "competition":
        if data.get("mode") not in ("race", "max"):
            raise AppError("bad_goal", "Режим соревнования: гонка до порога или максимум за период")
        if data["mode"] == "race" and not data.get("target_units"):
            raise AppError("bad_goal", "Для гонки укажите порог")


async def save_goal(
    session: AsyncSession, actor: Actor, goal_id: int | None, data: dict[str, Any]
) -> Goal:
    actor.require(Perm.GOALS_MANAGE)
    _validate(data)
    found = set(
        (
            await session.execute(select(Product.id).where(Product.id.in_(data["product_ids"])))
        ).scalars()
    )
    if missing := set(data["product_ids"]) - found:
        raise NotFound(f"Продукт не найден: {sorted(missing)}")
    people = await resolve_participants(session, data["participants_spec"])
    people -= set(data.get("exclusions") or [])
    if not people:
        raise AppError("no_participants", "В группе нет активных участников")
    scope = await scope_for(session, actor, Perm.GOALS_MANAGE)
    if scope is not ALL and not people <= scope:
        raise Forbidden("out_of_scope", "Часть участников вне вашей области")

    if goal_id is None:
        goal = Goal(created_by_id=actor.id, **data)
        session.add(goal)
        await session.flush()
        old = None
        new_people = True
    else:
        goal = await session.get(Goal, goal_id, with_for_update=True)
        if (
            goal is None
            or goal.status == GoalStatus.DELETED
            or not await _manages(session, actor, goal)
        ):
            raise NotFound("Цель не найдена")
        old = {k: getattr(goal, k) for k in data}
        new_people = (
            data["participants_spec"] != goal.participants_spec
            or data.get("exclusions") != goal.exclusions
        )
        for k, v in data.items():
            setattr(goal, k, v)
    if goal.freeze_participants and new_people:
        await session.execute(delete(GoalParticipant).where(GoalParticipant.goal_id == goal.id))
        for uid in people:
            session.add(GoalParticipant(goal_id=goal.id, user_id=uid))
    audit.record(
        session,
        category=Category.SETTINGS,
        action="goal.saved",
        actor_id=actor.id,
        object_type="goal",
        object_id=goal.id,
        old=old,
        new={**data, "participants": sorted(people)},
    )
    await session.commit()
    return goal


async def _manages(session: AsyncSession, actor: Actor, goal: Goal) -> bool:
    """Руководитель управляет целью, только если все её участники в его области."""
    if not actor.has(Perm.GOALS_MANAGE):
        return False
    scope = await scope_for(session, actor, Perm.GOALS_MANAGE)
    return scope is ALL or (await participants(session, goal)) <= scope


async def set_status(session: AsyncSession, actor: Actor, goal_id: int, status: GoalStatus) -> Goal:
    actor.require(Perm.GOALS_MANAGE)
    goal = await session.get(Goal, goal_id, with_for_update=True)
    if goal is None or not await _manages(session, actor, goal):
        raise NotFound("Цель не найдена")
    if goal.status != status:
        audit.record(
            session,
            category=Category.SETTINGS,
            action="goal.status_changed",
            actor_id=actor.id,
            object_type="goal",
            object_id=goal.id,
            old={"status": goal.status},
            new={"status": status},
        )
        goal.status = status
        await session.commit()
    return goal


async def participants(session: AsyncSession, goal: Goal) -> set[int]:
    if goal.freeze_participants:
        rows = await session.execute(
            select(GoalParticipant.user_id).where(GoalParticipant.goal_id == goal.id)
        )
        return set(rows.scalars())
    return await resolve_participants(session, goal.participants_spec) - set(goal.exclusions or [])


def _place(rows: list[dict[str, Any]], key, tie_break: str) -> None:
    """Места. При ничьей: «earliest» — выше тот, кто достиг результата раньше;
    «shared» — общее место."""
    place, prev = 0, None
    for i, r in enumerate(rows, start=1):
        k = key(r)
        if tie_break == "shared" and prev is not None and k == prev:
            r["place"] = place
        else:
            place = i
            r["place"] = i
        prev = k


async def results(session: AsyncSession, goal: Goal) -> dict[str, Any]:
    ids = await participants(session, goal)
    statuses = {
        "completed": DONE_STATUSES,  # все сделанные единицы продукта
        "approved": (TaskStatus.APPROVED, TaskStatus.PAID),  # допущенные к оплате
        "paid": (TaskStatus.PAID,),  # только вошедшие в подтверждённую выплату
    }[goal.counting]
    lo, hi = timeutil.msk_day_bounds(goal.period_from, goal.period_to)
    tasks = (
        await session.execute(
            select(Task.assignee_id, Task.units, Task.completed_at)
            .where(
                Task.assignee_id.in_(ids or {0}),
                Task.product_id.in_(goal.product_ids),
                Task.status.in_(statuses),
                Task.completed_at >= lo,
                Task.completed_at < hi,
            )
            .order_by(Task.completed_at, Task.id)
        )
    ).all()
    units: dict[int, int] = defaultdict(int)
    last_at: dict[int, datetime] = {}
    reached_at: dict[int, datetime] = {}
    for uid, n, at in tasks:
        units[uid] += n
        last_at[uid] = at
        if goal.target_units and uid not in reached_at and units[uid] >= goal.target_units:
            reached_at[uid] = at
    names = {
        u.id: u.full_name
        for u in (await session.execute(select(User).where(User.id.in_(ids or {0})))).scalars()
    }
    rows = [
        {
            "user_id": uid,
            "full_name": names.get(uid, f"#{uid}"),
            "units": units.get(uid, 0),
            "reached_at": reached_at.get(uid),
            "last_unit_at": last_at.get(uid),
            "done": bool(goal.target_units and units.get(uid, 0) >= goal.target_units),
        }
        for uid in ids
    ]
    far = datetime.max.replace(tzinfo=timeutil.MSK)
    if goal.kind == "competition" and goal.mode == "race":
        rows.sort(
            key=lambda r: (
                r["reached_at"] is None,
                r["reached_at"] or far,
                -r["units"],
                r["full_name"],
            )
        )
        _place(rows, lambda r: (r["reached_at"] is None, r["reached_at"]), goal.tie_break)
    else:
        rows.sort(key=lambda r: (-r["units"], r["last_unit_at"] or far, r["full_name"]))
        _place(rows, lambda r: r["units"], goal.tie_break)
    prizes = {p.get("place"): p.get("prize") for p in goal.prizes or []}
    for r in rows:
        eligible = goal.kind == "competition" and (
            goal.mode != "race" or r["reached_at"] is not None
        )
        r["prize"] = prizes.get(r["place"]) if eligible and r["units"] > 0 else None
    total = sum(r["units"] for r in rows)
    agg = {
        "participants": len(rows),
        "total_units": total,
        "average_units": round(total / len(rows), 2) if rows else 0,
        "min_units": min((r["units"] for r in rows), default=0),
    }
    if goal.scope == "team" and goal.target_units:
        value = {"total": total, "average": agg["average_units"], "min_each": agg["min_units"]}.get(
            goal.ranking, total
        )
        agg["team_progress"] = value
        agg["team_done"] = value >= goal.target_units
    return {"rows": rows, "aggregate": agg}


def goal_out(g: Goal) -> dict[str, Any]:
    return {k: getattr(g, k) for k in ("id", *FIELDS, "status", "created_at")}


async def visible_goals(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    q = (
        select(Goal)
        .where(Goal.status != GoalStatus.DELETED)
        .order_by(Goal.period_to.desc(), Goal.id.desc())
    )
    if not actor.has(Perm.GOALS_MANAGE):
        q = q.where(Goal.status == GoalStatus.ACTIVE)
    out = []
    for g in (await session.execute(q)).scalars():
        ids = await participants(session, g)
        mine = actor.id in ids
        manager = await _manages(session, actor, g)
        if not manager and g.status != GoalStatus.ACTIVE:
            continue
        if not manager and not (mine and g.visibility != "managers" or g.visibility == "all"):
            continue
        res = await results(session, g)
        me = next((r for r in res["rows"] if r["user_id"] == actor.id), None)
        out.append({**goal_out(g), "is_participant": mine, "my": me, "aggregate": res["aggregate"]})
    return out


async def goal_details(session: AsyncSession, actor: Actor, goal_id: int) -> dict[str, Any]:
    g = await session.get(Goal, goal_id)
    if g is None or g.status == GoalStatus.DELETED:
        raise NotFound("Цель не найдена")
    ids = await participants(session, g)
    if not await _manages(session, actor, g):
        if g.status != GoalStatus.ACTIVE or not (
            g.visibility == "all" or (actor.id in ids and g.visibility != "managers")
        ):
            raise NotFound("Цель не найдена")
    return {**goal_out(g), **(await results(session, g))}
