"""Этап 6: график, точки, сроки редактирования, вместимость, сводка, распред."""

import asyncio
from datetime import date, datetime, time, timedelta

import pytest

from app import timeutil
from tests.helpers import (
    OWNER_TG,
    auth,
    grant,
    make_active,
    owner_ready,
    ready_product,
    run_task,
    take,
)

# Среда, 11:00 МСК — «сейчас» во всех тестах графика.
NOW = datetime(2026, 10, 28, 11, 0, tzinfo=timeutil.MSK)
TODAY = NOW.date()


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(timeutil, "now_utc", lambda: state["now"])
    return state


async def _point(client, name="Тверская", capacity=None):
    r = await client.post(
        "/api/work-points", json={"name": name, "capacity": capacity}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _shift(
    client, tg, day: date, point_id, start="10:00", end="18:00", plans=None, user_id=None
):
    body = {
        "day": day.isoformat(),
        "start": start,
        "end": end,
        "point_id": point_id,
        "plans": plans or [],
    }
    if user_id:
        body["user_id"] = user_id
    return await client.post("/api/shifts", json=body, headers=auth(tg))


async def test_points_owner_only(client, clock):
    await owner_ready(client)
    await make_active(client, 70)
    r = await client.post("/api/work-points", json={"name": "X"}, headers=auth(70))
    assert r.status_code == 403
    p = await _point(client, capacity=2)
    assert p["capacity"] == 2
    assert len((await client.get("/api/work-points", headers=auth(70))).json()) == 1


async def test_future_shift_confirmed_today_pending(client, clock):
    await owner_ready(client)
    emp = await make_active(client, 71)
    prod = await ready_product(client, [emp], n_tasks=0)
    pt = await _point(client)
    tomorrow = TODAY + timedelta(days=1)
    r = await _shift(
        client, 71, tomorrow, pt["id"], plans=[{"product_id": prod["id"], "units": 20}]
    )
    assert r.status_code == 201 and r.json()["status"] == "confirmed"
    r = await _shift(client, 71, tomorrow, pt["id"])
    assert r.json()["code"] == "already_scheduled"

    r = await _shift(client, 71, TODAY, pt["id"])
    assert r.json()["status"] == "pending"
    sched = (await client.get("/api/my/schedule", headers=auth(71))).json()
    by_day = {d["day"]: d for d in sched["days"]}
    assert by_day[TODAY.isoformat()]["status"] == "pending"
    assert by_day[tomorrow.isoformat()]["status"] == "confirmed"
    # Прогноз: 20 единиц × 50 ₽.
    assert by_day[tomorrow.isoformat()]["shift"]["forecast_kop"] == 20 * 5000

    pending = (await client.get("/api/shifts/pending", headers=auth(OWNER_TG))).json()
    assert len(pending) == 1
    r = await client.post(f"/api/shifts/{pending[0]['id']}/confirm", headers=auth(OWNER_TG))
    assert r.json()["status"] == "confirmed"
    sched = (await client.get("/api/my/schedule", headers=auth(71))).json()
    assert {d["day"]: d for d in sched["days"]}[TODAY.isoformat()]["status"] == "working_today"

    # Вне окна (позже следующей недели) встать в график нельзя.
    far = timeutil.week_start(TODAY) + timedelta(days=14)
    assert (await _shift(client, 71, far, pt["id"])).json()["code"] == "out_of_window"


async def test_capacity_and_overlap(client, clock):
    await owner_ready(client)
    a = await make_active(client, 72)
    b = await make_active(client, 73)
    c = await make_active(client, 74)
    pt = await _point(client, capacity=1)
    day = TODAY + timedelta(days=2)
    assert (await _shift(client, 72, day, pt["id"], "10:00", "14:00")).status_code == 201
    r = await _shift(client, 73, day, pt["id"], "13:00", "18:00")
    assert r.status_code == 409 and r.json()["code"] == "point_full"
    # Не пересекается по времени — место есть.
    assert (await _shift(client, 73, day, pt["id"], "14:00", "18:00")).status_code == 201
    # Ожидающая заявка место не занимает: сегодняшняя смена c — pending.
    assert (await _shift(client, 74, TODAY, pt["id"], "10:00", "12:00")).json()[
        "status"
    ] == "pending"
    assert a and b and c


async def test_parallel_requests_for_last_place(client, clock):
    await owner_ready(client)
    ids = [await make_active(client, 80 + i, f"Имя{chr(1072 + i)}", "Тестов") for i in range(4)]
    pt = await _point(client, capacity=2)
    day = TODAY + timedelta(days=1)
    results = await asyncio.gather(*(_shift(client, 80 + i, day, pt["id"]) for i in range(4)))
    assert sorted(r.status_code for r in results) == [201, 201, 409, 409]
    assert ids


async def test_edit_deadlines(client, clock):
    await owner_ready(client)
    emp = await make_active(client, 75)
    prod = await ready_product(client, [emp], n_tasks=0)
    pt = await _point(client)
    pt2 = await _point(client, "Арбат")
    day = TODAY + timedelta(days=2)
    sid = (await _shift(client, 75, day, pt["id"])).json()["id"]

    # 22:30 накануне: план менять уже нельзя, время и точку — можно.
    clock["now"] = datetime.combine(day - timedelta(days=1), time(22, 30), tzinfo=timeutil.MSK)
    r = await client.put(
        f"/api/shifts/{sid}/plans",
        json={"plans": [{"product_id": prod["id"], "units": 5}]},
        headers=auth(75),
    )
    assert r.status_code == 409 and r.json()["code"] == "too_late"
    r = await client.patch(
        f"/api/shifts/{sid}",
        json={"start": "11:00", "end": "19:00", "point_id": pt2["id"]},
        headers=auth(75),
    )
    assert r.status_code == 200

    # После 00:00 дня смены — и время менять нельзя.
    clock["now"] = datetime.combine(day, time(0, 1), tzinfo=timeutil.MSK)
    r = await client.patch(
        f"/api/shifts/{sid}",
        json={"start": "12:00", "end": "19:00", "point_id": pt2["id"]},
        headers=auth(75),
    )
    assert r.json()["code"] == "too_late"
    # Руководитель может (с правом schedule.manage).
    r = await client.patch(
        f"/api/shifts/{sid}",
        json={"start": "12:00", "end": "19:00", "point_id": pt2["id"]},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200


async def test_day_statuses_fact_and_summary(client, clock):
    await owner_ready(client)
    emp = await make_active(client, 76, "Анна", "Работова")
    idle = await make_active(client, 77, "Борис", "Простоев")
    lead = await make_active(client, 78, role="admin")
    prod = await ready_product(client, [emp, idle], n_tasks=3, active_task_limit=3)
    pt = await _point(client)
    yesterday = TODAY - timedelta(days=1)

    # Вчерашние смены создаём «вчера».
    clock["now"] = datetime.combine(yesterday - timedelta(days=1), time(10, 0), tzinfo=timeutil.MSK)
    plans = [{"product_id": prod["id"], "units": 4}]
    assert (await _shift(client, 76, yesterday, pt["id"], plans=plans)).status_code == 201
    assert (await _shift(client, 77, yesterday, pt["id"], plans=plans)).status_code == 201

    # Сотрудник выполнил 2 задания вчера.
    clock["now"] = datetime.combine(yesterday, time(15, 0), tzinfo=timeutil.MSK)
    for _ in range(2):
        t = await take(client, 76, prod["id"])
        await run_task(client, 76, t["id"])
    from sqlalchemy import update

    from app.db import get_sessionmaker
    from app.models import Task

    async with get_sessionmaker()() as s:  # завершение задания пишется реальным временем
        await s.execute(
            update(Task).where(Task.assignee_id == emp).values(completed_at=clock["now"])
        )
        await s.commit()

    clock["now"] = NOW
    await client.post(
        "/api/day-off",
        json={"day": (TODAY + timedelta(days=3)).isoformat(), "on": True},
        headers=auth(77),
    )

    r = await client.get(
        f"/api/schedule/summary?start={yesterday}&end={TODAY + timedelta(days=3)}", headers=auth(78)
    )
    assert r.status_code == 403
    await grant(client, lead, "schedule.view")
    s = (
        await client.get(
            f"/api/schedule/summary?start={yesterday}&end={TODAY + timedelta(days=3)}",
            headers=auth(78),
        )
    ).json()
    rows = {(r["full_name"], r["day"]): r for r in s["rows"]}
    worked = rows[("Работова Анна", yesterday.isoformat())]
    assert (
        worked["status"] == "worked"
        and worked["actual_units"] == 2
        and worked["plan_percent"] == 50.0
    )
    # Смена была, но работы нет — это не «работал».
    assert rows[("Простоев Борис", yesterday.isoformat())]["status"] == "no_show"
    assert "day_off" in {r["status"] for r in s["rows"]}
    assert s["total"]["planned_units"] == 8 and s["total"]["actual_units"] == 2


async def test_distribution_text(client, clock):
    await owner_ready(client)
    emp = await make_active(client, 79, "Пётр", "Петров")
    prod = await ready_product(client, [emp], n_tasks=0)
    pt = await _point(client, "Тверская")
    day = TODAY + timedelta(days=1)
    await _shift(
        client, 79, day, pt["id"], "09:00", "17:00", [{"product_id": prod["id"], "units": 15}]
    )
    d = (await client.get(f"/api/schedule/distribution?day={day}", headers=auth(OWNER_TG))).json()
    assert d["people"] == 1
    assert "Тверская" in d["text"]
    assert "Петров Пётр 09:00–17:00 — Продукт А 15" in d["text"]
    assert "Итого: людей 1; план: Продукт А 15" in d["text"]


async def test_colleagues_visibility(client, clock):
    await owner_ready(client)
    await make_active(client, 90)
    await make_active(client, 91)
    pt = await _point(client)
    day = TODAY + timedelta(days=1)
    await _shift(client, 91, day, pt["id"])
    url = f"/api/schedule/colleagues?start={TODAY}&end={day}"
    assert (await client.get(url, headers=auth(90))).json()[
        "items"
    ] == []  # по умолчанию только свой
    await client.patch(
        "/api/settings", json={"schedule_visibility": "busy"}, headers=auth(OWNER_TG)
    )
    items = (await client.get(url, headers=auth(90))).json()["items"]
    assert len(items) == 1 and "point" not in items[0]
    await client.patch(
        "/api/settings", json={"schedule_visibility": "full"}, headers=auth(OWNER_TG)
    )
    items = (await client.get(url, headers=auth(90))).json()["items"]
    assert items[0]["point"] == "Тверская"
