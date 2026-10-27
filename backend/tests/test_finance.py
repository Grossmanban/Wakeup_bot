"""Этап 7: журнал, штрафы, удержания, бонусы, «налог на лень», выплаты."""

import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app import timeutil
from app.db import get_sessionmaker
from app.models import Task, TaskStatus
from app.services import finance
from tests.helpers import (
    OWNER_TG,
    auth,
    grant,
    make_active,
    moderate,
    owner_ready,
    ready_product,
    run_task,
    take,
)

TODAY = timeutil.msk_today()
PERIOD = f"start={TODAY - timedelta(days=30)}&end={TODAY}"


def key() -> str:
    return uuid.uuid4().hex


async def _approved(client, tg, product_id, n):
    ids = []
    for _ in range(n):
        t = await take(client, tg, product_id)
        await run_task(client, tg, t["id"])
        assert (await moderate(client, t["id"], "approve")).status_code == 200
        ids.append(t["id"])
    return ids


async def _preview(client, uid):
    r = await client.get(f"/api/users/{uid}/payout-preview?{PERIOD}", headers=auth(OWNER_TG))
    assert r.status_code == 200, r.text
    return r.json()


async def _pay(client, uid, k=None, expected=None):
    return await client.post(
        f"/api/users/{uid}/payouts",
        json={
            "start": str(TODAY - timedelta(days=30)),
            "end": str(TODAY),
            "idempotency_key": k or key(),
            "expected_total_kop": expected,
        },
        headers=auth(OWNER_TG),
    )


async def test_payout_with_bonus_penalty_and_idempotency(client, session):
    await owner_ready(client)
    emp = await make_active(client, 100)
    p = await ready_product(client, [emp], n_tasks=3, active_task_limit=3)
    ids = await _approved(client, 100, p["id"], 3)
    r = await client.post(
        f"/api/users/{emp}/bonuses",
        json={"amount_kop": 2000, "reason": "Лучший день", "request_key": "bonus-001"},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 201
    # Повтор того же запроса не создаёт второй бонус.
    r2 = await client.post(
        f"/api/users/{emp}/bonuses",
        json={"amount_kop": 2000, "reason": "Лучший день", "request_key": "bonus-001"},
        headers=auth(OWNER_TG),
    )
    assert r2.json()["id"] == r.json()["id"]
    await client.post(
        f"/api/users/{emp}/penalties",
        json={"amount_kop": 3000, "reason": "Опоздание", "request_key": "pen-0001"},
        headers=auth(OWNER_TG),
    )

    pv = await _preview(client, emp)
    assert pv["earnings_kop"] == 15000 and pv["bonuses_kop"] == 2000
    assert pv["deduction_kop"] == 3000 and pv["total_kop"] == 14000
    assert pv["products"][0]["units"] == 3 and pv["products"][0]["rates"] == [5000]

    k = key()
    r = await _pay(client, emp, k, expected=14000)
    assert r.status_code == 201 and r.json()["amount_kop"] == 14000
    # Повторное нажатие — та же выплата.
    again = await _pay(client, emp, k)
    assert again.json()["id"] == r.json()["id"]
    # Новый запрос — выплачивать нечего, вторая выплата не создаётся.
    r3 = await _pay(client, emp)
    assert r3.status_code == 409 and r3.json()["code"] == "nothing"

    statuses = (await session.execute(select(Task.status).where(Task.id.in_(ids)))).scalars().all()
    assert set(statuses) == {TaskStatus.PAID}
    ov = (await client.get("/api/my/finance", headers=auth(100))).json()
    assert ov["unpaid_kop"] == 0 and ov["debt_kop"] == 0
    assert len(ov["payouts"]) == 1


async def test_debt_split_across_payouts_oldest_first(client):
    await owner_ready(client)
    emp = await make_active(client, 101)
    p = await ready_product(client, [emp], n_tasks=6, active_task_limit=6)
    await _approved(client, 101, p["id"], 3)  # 150 ₽
    for i, amount in enumerate((10000, 10000)):  # два штрафа по 100 ₽
        await client.post(
            f"/api/users/{emp}/penalties",
            json={"amount_kop": amount, "reason": f"Штраф {i}", "request_key": f"pen-split-{i}"},
            headers=auth(OWNER_TG),
        )
    pv = await _preview(client, emp)
    assert pv["total_kop"] == 0  # выплата не бывает отрицательной
    assert pv["deduction_kop"] == 15000 and pv["debt_left_kop"] == 5000
    assert [d["deducted_kop"] for d in pv["debts"]] == [10000, 5000]  # старый долг первым
    assert (await _pay(client, emp)).status_code == 201

    await _approved(client, 101, p["id"], 3)  # ещё 150 ₽
    pv = await _preview(client, emp)
    assert pv["deduction_kop"] == 5000 and pv["total_kop"] == 10000 and pv["debt_left_kop"] == 0
    assert (await _pay(client, emp)).status_code == 201
    ov = (await client.get(f"/api/users/{emp}/finance", headers=auth(OWNER_TG))).json()
    assert ov["debt_kop"] == 0


async def test_unit_penalty_uses_current_rate(client):
    await owner_ready(client)
    emp = await make_active(client, 102)
    p = await ready_product(client, [emp], n_tasks=0)
    r = await client.post(
        f"/api/users/{emp}/penalties",
        json={"units": 2, "product_id": p["id"], "reason": "Брак", "request_key": "pen-units1"},
        headers=auth(OWNER_TG),
    )
    assert r.json()["amount_kop"] == -10000 and r.json()["rate_kop"] == 5000


async def test_reversal_after_payout_becomes_debt(client):
    await owner_ready(client)
    emp = await make_active(client, 103)
    p = await ready_product(client, [emp], n_tasks=3, active_task_limit=3)
    ids = await _approved(client, 103, p["id"], 2)
    assert (await _pay(client, emp)).json()["amount_kop"] == 10000
    # Задание оказалось браком уже после выплаты.
    assert (await moderate(client, ids[0], "reject", "Подделка")).status_code == 200
    await _approved(client, 103, p["id"], 1)
    pv = await _preview(client, emp)
    assert pv["deduction_kop"] == 5000 and pv["total_kop"] == 0


async def test_lazy_tax(client, session):
    await owner_ready(client)
    emp = await make_active(client, 104)
    p = await ready_product(client, [emp], n_tasks=6, active_task_limit=6)
    day = TODAY - timedelta(days=1)
    r = await client.post(
        "/api/lazy-rules",
        json={
            "product_id": p["id"],
            "min_daily_units": 5,
            "reduced_rate_kop": 2000,
            "period_from": str(day),
        },
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 201
    rule = r.json()

    # Вчера выполнено 2 единицы при минимуме 5 → 3 единицы по сниженной ставке.
    ids = await _approved(client, 104, p["id"], 2)
    yesterday_noon = timeutil.msk_at(day, datetime.min.time().replace(hour=12))
    async with get_sessionmaker()() as s:
        await s.execute(update(Task).where(Task.id.in_(ids)).values(completed_at=yesterday_noon))
        from app.models import LedgerEntry

        await s.execute(
            update(LedgerEntry)
            .where(LedgerEntry.task_id.in_(ids))
            .values(occurred_at=yesterday_noon)
        )
        await s.commit()
    async with get_sessionmaker()() as s:
        assert await finance.close_lazy_day(s, day) == 1
    async with get_sessionmaker()() as s:
        assert await finance.close_lazy_day(s, day) == 0  # повторный запуск без дублей

    # Сегодня ещё 2 единицы. В выплате 4 единицы: 3 по 20 ₽, 1 по 50 ₽.
    await _approved(client, 104, p["id"], 2)
    pv = await _preview(client, emp)
    assert pv["earnings_kop"] == 4 * 5000
    assert pv["lazy_kop"] == 3 * (5000 - 2000)
    assert pv["total_kop"] == 3 * 2000 + 1 * 5000
    assert pv["products"][0]["reduced_units"] == 3 and pv["products"][0]["normal_units"] == 1
    assert (await _pay(client, emp)).status_code == 201
    ov = (await client.get("/api/my/finance", headers=auth(104))).json()
    assert ov["lazy"] == {**ov["lazy"], "accrued": 3, "used": 3, "left": 0}

    # Персональное исключение: следующий день не начисляется.
    await client.post(
        f"/api/lazy-rules/{rule['id']}/exemptions",
        json={"user_id": emp, "on": True},
        headers=auth(OWNER_TG),
    )
    async with get_sessionmaker()() as s:
        assert await finance.close_lazy_day(s, TODAY) == 0


async def test_lazy_remainder_carries_over(client):
    await owner_ready(client)
    emp = await make_active(client, 105)
    p = await ready_product(client, [emp], n_tasks=3, active_task_limit=3)
    day = TODAY - timedelta(days=2)
    await client.post(
        "/api/lazy-rules",
        json={
            "product_id": p["id"],
            "min_daily_units": 10,
            "reduced_rate_kop": 1000,
            "period_from": str(day),
        },
        headers=auth(OWNER_TG),
    )
    # День с подтверждённой сменой по продукту, но без работы → недобор 10.
    from app.models import Shift, ShiftPlan, ShiftStatus, WorkPoint

    async with get_sessionmaker()() as s:
        pt = WorkPoint(name="Тест")
        s.add(pt)
        await s.flush()
        sh = Shift(
            user_id=emp,
            day=day,
            start_time=datetime.min.time().replace(hour=10),
            end_time=datetime.min.time().replace(hour=18),
            point_id=pt.id,
            status=ShiftStatus.CONFIRMED,
        )
        s.add(sh)
        await s.flush()
        s.add(ShiftPlan(shift_id=sh.id, product_id=p["id"], units=10))
        await s.commit()
    async with get_sessionmaker()() as s:
        assert await finance.close_lazy_day(s, day) == 1
    await _approved(client, 105, p["id"], 3)
    pv = await _preview(client, emp)
    assert pv["total_kop"] == 3 * 1000
    await _pay(client, emp)
    ov = (await client.get("/api/my/finance", headers=auth(105))).json()
    assert ov["lazy"]["left"] == 7


async def test_auto_bonus_once(client, session):
    await owner_ready(client)
    emp = await make_active(client, 106)
    p = await ready_product(client, [emp], n_tasks=4, active_task_limit=4)
    r = await client.post(
        "/api/bonus-rules",
        json={
            "name": "Ударник",
            "amount_kop": 50000,
            "conditions": [{"product_id": p["id"], "min_units": 2}],
            "min_quality": 90,
            "period_from": str(TODAY - timedelta(days=1)),
            "period_to": str(TODAY + timedelta(days=1)),
        },
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 201
    await _approved(client, 106, p["id"], 1)
    ov = (await client.get("/api/my/finance", headers=auth(106))).json()
    assert not [e for e in ov["entries"] if e["kind"] == "bonus"]
    await _approved(client, 106, p["id"], 2)
    ov = (await client.get("/api/my/finance", headers=auth(106))).json()
    bonuses = [e for e in ov["entries"] if e["kind"] == "bonus"]
    assert len(bonuses) == 1 and bonuses[0]["amount_kop"] == 50000


async def test_auto_bonus_requires_quality(client):
    await owner_ready(client)
    emp = await make_active(client, 107)
    p = await ready_product(client, [emp], n_tasks=3, active_task_limit=3)
    await client.post(
        "/api/bonus-rules",
        json={
            "name": "Качество",
            "amount_kop": 10000,
            "conditions": [{"product_id": p["id"], "min_units": 2}],
            "min_quality": 90,
            "period_from": str(TODAY),
            "period_to": str(TODAY),
        },
        headers=auth(OWNER_TG),
    )
    t = await take(client, 107, p["id"])
    await run_task(client, 107, t["id"])
    await moderate(client, t["id"], "reject", "Брак")
    await _approved(client, 107, p["id"], 2)  # 2 из 3 — качество 67%
    ov = (await client.get("/api/my/finance", headers=auth(107))).json()
    assert not [e for e in ov["entries"] if e["kind"] == "bonus"]


async def test_parallel_payouts_not_duplicated(client):
    await owner_ready(client)
    emp = await make_active(client, 108)
    p = await ready_product(client, [emp], n_tasks=2, active_task_limit=2)
    await _approved(client, 108, p["id"], 2)
    results = await asyncio.gather(*(_pay(client, emp) for _ in range(4)))
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409]


async def test_finance_permissions_and_changed_data(client):
    await owner_ready(client)
    emp = await make_active(client, 109)
    acc = await make_active(client, 110, role="admin")
    p = await ready_product(client, [emp], n_tasks=2, active_task_limit=2)
    await _approved(client, 109, p["id"], 1)
    # Свои финансы сотрудник смотрит через /my/finance, чужие — только с правом.
    assert (await client.get(f"/api/users/{emp}/finance", headers=auth(109))).status_code == 403
    assert (
        await client.get(f"/api/users/{emp}/payout-preview?{PERIOD}", headers=auth(110))
    ).status_code == 403
    await grant(client, acc, "finance.view")
    assert (await client.get(f"/api/users/{emp}/finance", headers=auth(110))).status_code == 200
    assert (
        await client.get(f"/api/users/{emp}/payout-preview?{PERIOD}", headers=auth(110))
    ).status_code == 403
    pv = await _preview(client, emp)
    await _approved(client, 109, p["id"], 1)  # данные изменились после предпросмотра
    r = await _pay(client, emp, expected=pv["total_kop"])
    assert r.status_code == 409 and r.json()["code"] == "changed"


async def test_scheduled_lazy_close_runs_once(client):
    from app import jobs

    await owner_ready(client)
    night = timeutil.msk_at(TODAY, datetime.min.time().replace(minute=15))
    first = await jobs.run_due(get_sessionmaker(), night)
    second = await jobs.run_due(get_sessionmaker(), night)
    assert first == [f"lazy_close:{TODAY - timedelta(days=1)}"]
    assert second == []
    # До 00:10 закрытие дня не запускается.
    early = timeutil.msk_at(TODAY + timedelta(days=1), datetime.min.time().replace(minute=5))
    assert await jobs.run_due(get_sessionmaker(), early) == []


async def test_unpaid_list(client):
    await owner_ready(client)
    emp = await make_active(client, 111)
    p = await ready_product(client, [emp], n_tasks=1)
    await _approved(client, 111, p["id"], 1)
    rows = (await client.get("/api/finance/unpaid", headers=auth(OWNER_TG))).json()
    assert [(r["user_id"], r["unpaid_kop"]) for r in rows] == [(emp, 5000)]
    assert (await client.get("/api/finance/unpaid", headers=auth(111))).status_code == 403
