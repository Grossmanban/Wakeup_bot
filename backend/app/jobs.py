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
from app.services import finance

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

    for hook in EXTRA_JOBS:
        await hook(sessionmaker, now, run)
    return ran


# Дополнительные задачи модулей (фотоотчёты, архив аудита) регистрируются здесь.
EXTRA_JOBS: list[Callable[..., Awaitable[None]]] = []
