"""Этап 13: сквозной сценарий через все модули и сверка итогов с исходными данными.

Соответствие приёмочным пунктам ТЗ 21 — docs/acceptance.md.
"""

from datetime import timedelta

from sqlalchemy import func, select, update

from app import timeutil
from app.db import get_sessionmaker
from app.models import AuditEvent, EntryKind, LedgerEntry, Task, TaskStatus
from app.services import photoreports
from bot.outbox_worker import process_due
from tests.helpers import (
    OWNER_TG,
    auth,
    grant,
    import_tasks,
    moderate,
    owner_ready,
    photo,
    register,
)

FLOW = [{"title": "Проверка", "checklist": ["Сверил адрес"], "min_seconds": 1, "photo": "none"}]


async def test_end_to_end(client, gateway, session):
    h = auth(OWNER_TG)
    await owner_ready(client)

    # 1. Регистрация и одобрение.
    req = await register(client, 200, "Мария", "Сквозная")
    await client.post(f"/api/registrations/{req['id']}/approve", headers=h)
    emp = req["user_id"]

    # 2. Продукт, ставка, доступ, группа фотоотчётов, импорт.
    p = (
        await client.post(
            "/api/products",
            json={"name": "Сквозной", "flow": FLOW, "final_photo_required": True},
            headers=h,
        )
    ).json()
    await client.put(
        f"/api/products/{p['id']}/rates/roles/employee", json={"amount_kop": 7000}, headers=h
    )
    await client.put(f"/api/users/{emp}/products", json={"ids": [p["id"]]}, headers=h)
    g = (await client.post("/api/groups", json={"chat_id": -9001}, headers=h)).json()
    await client.post(
        "/api/photoreports/routes",
        json={"kind": "product", "product_id": p["id"], "group_id": g["id"]},
        headers=h,
    )
    await import_tasks(client, p["id"], ["E1", "E2", "E3"])

    # 3. Выполнение трёх заданий с таймером и фото.
    done = []
    for _ in range(3):
        t = (await client.post(f"/api/my/products/{p['id']}/take", headers=auth(200))).json()
        e = auth(200)
        await client.post(f"/api/my/tasks/{t['id']}/open", headers=e)
        await client.post(f"/api/my/tasks/{t['id']}/found", headers=e)
        async with get_sessionmaker()() as s:
            await s.execute(
                update(Task)
                .where(Task.id == t["id"])
                .values(stage_started_at=Task.stage_started_at - timedelta(seconds=5))
            )
            await s.commit()
        r = await client.post(
            f"/api/my/tasks/{t['id']}/stages/0/complete", json={"checklist": [True]}, headers=e
        )
        assert r.status_code == 200, r.text
        assert (await photo(client, 200, t["id"])).status_code == 200
        v = (await client.post(f"/api/my/tasks/{t['id']}/complete", headers=e)).json()
        assert v["task"]["status"] == "moderation"
        done.append(t["id"])

    # 4. Модерация: два допущено, одно отклонено.
    await moderate(client, done[0], "approve")
    await moderate(client, done[1], "approve")
    await moderate(client, done[2], "reject", "Брак")

    # 5. Фотоотчёты ушли в группу продукта.
    await process_due(get_sessionmaker(), gateway.send)
    assert len([x for x in gateway.sent if x[0] == "sendPhoto" and x[1] == -9001]) == 3

    # 6. Статистика сходится с исходными данными.
    today = timeutil.msk_today()
    st = (await client.get(f"/api/stats?start={today}&end={today}", headers=h)).json()
    u = next(x for x in st["users"] if x["user_id"] == emp)
    tasks = (await session.execute(select(Task).where(Task.assignee_id == emp))).scalars().all()
    ledger = await session.scalar(
        select(func.sum(LedgerEntry.amount_kop)).where(
            LedgerEntry.user_id == emp,
            LedgerEntry.kind.in_([EntryKind.EARNING, EntryKind.EARNING_REVERSAL]),
        )
    )
    assert u["units"] == sum(t.units for t in tasks) == 3
    assert (
        u["approved_units"] == 2 and u["rejected_units"] == 1 and u["quality"] == round(200 / 3, 1)
    )
    assert u["earnings_kop"] == ledger == 2 * 7000

    # 7. Выплата: сумма = начисления; задания «Оплачено».
    pv = (
        await client.get(f"/api/users/{emp}/payout-preview?start={today}&end={today}", headers=h)
    ).json()
    assert pv["total_kop"] == 14000
    r = await client.post(
        f"/api/users/{emp}/payouts",
        json={
            "start": str(today),
            "end": str(today),
            "idempotency_key": "e2e-payout-1",
            "expected_total_kop": 14000,
        },
        headers=h,
    )
    assert r.status_code == 201
    paid = await session.scalar(
        select(func.count()).where(Task.assignee_id == emp, Task.status == TaskStatus.PAID)
    )
    assert paid == 2

    # 8. Сводка дня из БД совпадает с фактом.
    async with get_sessionmaker()() as s:
        day = await photoreports.day_stats(s, today)
    assert (day["photos"], day["unique_tasks"], day["units"], day["approved"], day["rejected"]) == (
        3,
        3,
        3,
        2,
        1,
    )

    # 9. Ключевые действия попали в журнал аудита.
    actions = set((await session.execute(select(AuditEvent.action))).scalars())
    for a in (
        "registration.approved",
        "product_access.changed",
        "tasks.imported",
        "moderation.approve",
        "moderation.reject",
        "payout.confirmed",
    ):
        assert a in actions, a

    # 10. Карточка сотрудника.
    s = (await client.get(f"/api/users/{emp}/summary", headers=h)).json()
    assert s["tasks"]["paid"] == 2 and s["tasks"]["rejected"] == 1 and s["newbie"]["label"] == "1/3"


async def test_disabled_user_cannot_work(client):
    """Отключённый аккаунт не работает в приложении (ТЗ 2.3)."""
    await owner_ready(client)
    req = await register(client, 201, "Олег", "Отключённый")
    await client.post(f"/api/registrations/{req['id']}/approve", headers=auth(OWNER_TG))
    await client.put(
        f"/api/users/{req['user_id']}/status", json={"status": "disabled"}, headers=auth(OWNER_TG)
    )
    assert (await client.get("/api/my/products", headers=auth(201))).status_code == 403
    assert (await client.get("/api/my/schedule", headers=auth(201))).status_code == 403
    me = (await client.get("/api/me", headers=auth(201))).json()
    assert me["message"] == "Ваш аккаунт отключён. Обратитесь к администрации для его включения"


async def test_permissions_are_enforced_on_every_module(client):
    """Админ без разрешений не может ничего, кроме своих данных."""
    await owner_ready(client)
    req = await register(client, 202, "Анна", "Безправная")
    await client.post(f"/api/registrations/{req['id']}/approve", headers=auth(OWNER_TG))
    await client.put(
        f"/api/users/{req['user_id']}/role", json={"role": "admin"}, headers=auth(OWNER_TG)
    )
    a = auth(202)
    forbidden = [
        ("GET", "/api/registrations"),
        ("GET", "/api/products"),
        ("GET", "/api/tasks"),
        ("GET", "/api/moderation"),
        ("GET", "/api/manual-review"),
        ("GET", "/api/shifts/pending"),
        ("GET", f"/api/schedule/summary?start={timeutil.msk_today()}&end={timeutil.msk_today()}"),
        ("GET", "/api/finance/unpaid"),
        ("GET", "/api/bonus-rules"),
        ("GET", "/api/lazy-rules"),
        ("GET", "/api/candidates"),
        ("GET", "/api/trainees"),
        ("GET", "/api/stats?preset=today"),
        ("GET", "/api/groups/overview"),
        ("GET", "/api/broadcasts"),
        ("GET", "/api/audit"),
        ("GET", "/api/settings"),
        ("GET", "/api/photoreports"),
        ("GET", "/api/permissions/catalog"),
    ]
    for method, url in forbidden:
        r = await client.request(method, url, headers=a)
        assert r.status_code == 403, (url, r.status_code, r.text)
    # После выдачи одного разрешения открывается ровно один раздел.
    await grant(client, req["user_id"], "moderation.review")
    assert (await client.get("/api/moderation", headers=a)).status_code == 200
    assert (await client.get("/api/manual-review", headers=a)).status_code == 403
