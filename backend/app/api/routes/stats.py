import secrets
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app import timeutil
from app.api.deps import ActorDep, SessionDep, active_actor
from app.errors import AppError, NotFound
from app.models import DownloadToken, TaskStatus, User
from app.permissions import Perm
from app.services import stats as svc

router = APIRouter(tags=["stats"])


def _filters(
    start: date | None,
    end: date | None,
    preset: str | None,
    user_ids: list[int] | None,
    network_id: int | None,
    coach_id: int | None,
    instructor_id: int | None,
    product_ids: list[int] | None,
    statuses: list[TaskStatus] | None,
) -> svc.Filters:
    if preset:
        start, end = svc.period_preset(preset)
    if start is None or end is None:
        raise AppError("bad_period", "Укажите период")
    if end < start:
        raise AppError("bad_period", "Конец периода раньше начала")
    return svc.Filters(
        start=start,
        end=end,
        user_ids=user_ids,
        network_id=network_id,
        coach_id=coach_id,
        instructor_id=instructor_id,
        product_ids=product_ids,
        statuses=statuses,
    )


Ids = Annotated[list[int] | None, Query()]
Statuses = Annotated[list[TaskStatus] | None, Query()]


@router.get("/stats")
async def stats(
    session: SessionDep,
    actor: ActorDep,
    start: date | None = None,
    end: date | None = None,
    preset: str | None = None,
    user_ids: Ids = None,
    network_id: int | None = None,
    coach_id: int | None = None,
    instructor_id: int | None = None,
    product_ids: Ids = None,
    statuses: Statuses = None,
    sort: str = "units",
):
    f = _filters(
        start, end, preset, user_ids, network_id, coach_id, instructor_id, product_ids, statuses
    )
    data = await svc.compute(session, actor, f)
    data["users"] = svc.sort_users(data["users"], sort)
    return data


@router.get("/my/stats")
async def my_stats(
    session: SessionDep,
    actor: ActorDep,
    start: date | None = None,
    end: date | None = None,
    preset: str | None = "month",
    product_ids: Ids = None,
):
    f = _filters(start, end, None if start else preset, None, None, None, None, product_ids, None)
    return await svc.compute(session, actor, f, self_only=True)


@router.get("/stats/tasks")
async def stats_tasks(
    session: SessionDep,
    actor: ActorDep,
    start: date | None = None,
    end: date | None = None,
    preset: str | None = None,
    user_ids: Ids = None,
    network_id: int | None = None,
    coach_id: int | None = None,
    instructor_id: int | None = None,
    product_ids: Ids = None,
    statuses: Statuses = None,
    mine: bool = False,
):
    f = _filters(
        start, end, preset, user_ids, network_id, coach_id, instructor_id, product_ids, statuses
    )
    return await svc.drilldown(session, actor, f, self_only=mine)


async def _export(session, actor, kind, format, f, sort):
    if kind == "users":
        data = await svc.compute(session, actor, f)
        rows, cols = svc.with_rub(svc.sort_users(data["users"], sort)), svc.USER_COLUMNS
    else:
        rows, cols = (
            svc.with_rub(await svc.drilldown(session, actor, f, limit=100000)),
            svc.TASK_COLUMNS,
        )
    body, media = svc.export(rows, cols, format)
    name = f"stats_{kind}_{f.start:%Y%m%d}-{f.end:%Y%m%d}.{format}"
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.get("/stats/export")
async def stats_export(
    session: SessionDep,
    actor: ActorDep,
    kind: Literal["users", "tasks"] = "users",
    format: Literal["xlsx", "csv"] = "xlsx",
    start: date | None = None,
    end: date | None = None,
    preset: str | None = None,
    user_ids: Ids = None,
    network_id: int | None = None,
    coach_id: int | None = None,
    instructor_id: int | None = None,
    product_ids: Ids = None,
    statuses: Statuses = None,
    sort: str = "units",
):
    actor.require(Perm.EXPORT)
    f = _filters(
        start, end, preset, user_ids, network_id, coach_id, instructor_id, product_ids, statuses
    )
    return await _export(session, actor, kind, format, f, sort)


class ExportLinkIn(BaseModel):
    kind: Literal["users", "tasks"] = "users"
    format: Literal["xlsx", "csv"] = "xlsx"
    start: date | None = None
    end: date | None = None
    preset: str | None = None
    user_ids: list[int] | None = None
    network_id: int | None = None
    coach_id: int | None = None
    instructor_id: int | None = None
    product_ids: list[int] | None = None
    statuses: list[TaskStatus] | None = None
    sort: str = "units"


@router.post("/stats/export-link")
async def export_link(body: ExportLinkIn, session: SessionDep, actor: ActorDep):
    """Ссылка на файл на 10 минут — для скачивания средствами Telegram без заголовка авторизации."""
    actor.require(Perm.EXPORT)
    _filters(body.start, body.end, body.preset, None, None, None, None, None, None)
    token = secrets.token_urlsafe(32)
    session.add(
        DownloadToken(
            token=token,
            user_id=actor.id,
            kind="stats",
            params=body.model_dump(mode="json"),
            expires_at=timeutil.now_utc() + timedelta(minutes=10),
        )
    )
    await session.commit()
    return {"url": f"/api/downloads/{token}", "file_name": f"stats_{body.kind}.{body.format}"}


@router.get("/downloads/{token}")
async def download(token: str, session: SessionDep):
    row = await session.get(DownloadToken, token)
    if row is None or row.expires_at < timeutil.now_utc():
        raise NotFound("Ссылка устарела — сформируйте выгрузку заново")
    user = await session.get(User, row.user_id)
    # Права проверяются заново на момент скачивания.
    actor = await active_actor(session, user)
    actor.require(Perm.EXPORT)
    p = ExportLinkIn.model_validate(row.params)
    f = _filters(
        p.start,
        p.end,
        p.preset,
        p.user_ids,
        p.network_id,
        p.coach_id,
        p.instructor_id,
        p.product_ids,
        p.statuses,
    )
    return await _export(session, actor, p.kind, p.format, f, p.sort)
