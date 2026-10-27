"""Фоновые задачи по расписанию (МСК). Запускаются из процесса бота раз в минуту.

Каждая задача сама по себе идемпотентна (уникальные ключи в БД), а отметка в job_runs
лишь избавляет от лишней работы: повторный запуск ничего не дублирует.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import timeutil
from app.models import JobRun
from app.services import audit_archive, finance, groups, photoreports
from app.services import settings as settings_svc

log = logging.getLogger(__name__)

Job = Callable[[AsyncSession], Awaitable[Any]]


async def once(
    sessionmaker: async_sessionmaker[AsyncSession], name: str, run_key: str, job: Job
) -> bool:
    async with sessionmaker() as session:
        done = await session.scalar(
            select(JobRun.name).where(JobRun.name == name, JobRun.run_key == run_key)
        )
        if done:
            return False
    async with sessionmaker() as session:
        result = await job(session)
    async with sessionmaker() as session:
        await session.execute(
            insert(JobRun)
            .values(name=name, run_key=run_key, result={"result": result})
            .on_conflict_do_nothing()
        )
        await session.commit()
    log.info("job %s %s done: %s", name, run_key, result)
    return True


async def _setting(sessionmaker: async_sessionmaker[AsyncSession], key: str) -> Any:
    async with sessionmaker() as session:
        return await settings_svc.get(session, key)


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


async def run_due(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> list[str]:
    now = timeutil.to_msk(now or timeutil.now_utc())
    today = now.date()
    yesterday = today - timedelta(days=1)
    ran: list[str] = []

    async def run(name: str, key: str, job: Job) -> None:
        try:
            if await once(sessionmaker, name, key, job):
                ran.append(f"{name}:{key}")
        except Exception:
            log.exception("job %s %s failed", name, key)

    # «Налог на лень»: недобор за прошедший день фиксируется после его окончания.
    if now.time() >= time(0, 10):
        await run(
            "lazy_close", yesterday.isoformat(), lambda s: finance.close_lazy_day(s, yesterday)
        )

    # Новички: пересчёт рабочих дней и даты перехода с записью изменений в аудит.
    if now.time() >= time(0, 20):
        await run("newbie_recalc", today.isoformat(), groups.recalc_all)

    # Фотоотчёты: сначала итоги прошедшего дня, затем заголовок нового (ТЗ 17.3, 17.4).
    summary_at = _hm(await _setting(sessionmaker, "daily_summary_time"))
    if now.time() >= summary_at:
        await run(
            "photo_summary",
            yesterday.isoformat(),
            lambda s: photoreports.post_summaries(s, yesterday),
        )
        await run("photo_header", today.isoformat(), lambda s: photoreports.post_headers(s, today))
        # Корректировки прошлых сводок — раз в час.
        await run(
            "photo_corrections",
            now.strftime("%Y-%m-%dT%H"),
            lambda s: photoreports.post_corrections(s, today),
        )

    # Архив аудита в Telegram — каждую минуту, порциями.
    try:
        async with sessionmaker() as session:
            n = await audit_archive.export_batch(session)
        if n:
            ran.append(f"audit_archive:{n}")
    except Exception:
        log.exception("audit archive export failed")

    for hook in EXTRA_JOBS:
        await hook(sessionmaker, now, run)
    return ran


# Дополнительные задачи модулей (фотоотчёты, архив аудита) регистрируются здесь.
EXTRA_JOBS: list[Callable[..., Awaitable[None]]] = []
