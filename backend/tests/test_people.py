"""Этап 8: цели и соревнования, рефералы, кандидаты, обучение."""

from datetime import timedelta

from sqlalchemy import select

from app import timeutil
from app.db import get_sessionmaker
from app.models import EntryKind, LedgerEntry, TelegramOutbox, User
from app.security.telegram_auth import TelegramUser
from app.services import candidates, users
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


async def _approve(client, tg, pid, n):
    for _ in range(n):
        t = await take(client, tg, pid)
        await run_task(client, tg, t["id"])
        await moderate(client, t["id"], "approve")


def _goal(**kw):
    base = {
        "title": "Октябрь",
        "kind": "goal",
        "participants_spec": {"type": "users", "ids": []},
        "product_ids": [],
        "target_units": 2,
        "period_from": str(TODAY - timedelta(days=1)),
        "period_to": str(TODAY + timedelta(days=1)),
    }
    return {**base, **kw}


async def test_goal_progress_and_no_double_counting(client):
    await owner_ready(client)
    a = await make_active(client, 120, "Анна", "Первая")
    b = await make_active(client, 121, "Борис", "Второй")
    p = await ready_product(client, [a, b], n_tasks=5, active_task_limit=5)
    # Один человек указан дважды — считается один раз.
    r = await client.post(
        "/api/goals",
        json=_goal(participants_spec={"type": "custom", "ids": [a, a, b]}, product_ids=[p["id"]]),
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    await _approve(client, 120, p["id"], 2)
    await _approve(client, 121, p["id"], 1)
    d = (await client.get(f"/api/goals/{gid}", headers=auth(120))).json()
    assert d["aggregate"]["participants"] == 2 and d["aggregate"]["total_units"] == 3
    rows = {r["full_name"]: r for r in d["rows"]}
    assert rows["Первая Анна"]["done"] and not rows["Второй Борис"]["done"]
    mine = (await client.get("/api/goals", headers=auth(121))).json()
    assert mine[0]["my"]["units"] == 1
    # Не участник при видимости «участники» цель не видит.
    await make_active(client, 122)
    assert (await client.get("/api/goals", headers=auth(122))).json() == []
    assert (await client.get(f"/api/goals/{gid}", headers=auth(122))).status_code == 404


async def test_race_and_tie_rules(client):
    await owner_ready(client)
    a = await make_active(client, 123, "Анна", "Быстрая")
    b = await make_active(client, 124, "Борис", "Медленный")
    p = await ready_product(client, [a, b], n_tasks=6, active_task_limit=6)
    r = await client.post(
        "/api/goals",
        json=_goal(
            kind="competition",
            mode="race",
            target_units=2,
            product_ids=[p["id"]],
            participants_spec={"type": "users", "ids": [a, b]},
            prizes=[{"place": 1, "prize": "Сертификат"}],
        ),
        headers=auth(OWNER_TG),
    )
    gid = r.json()["id"]
    await _approve(client, 123, p["id"], 2)
    await _approve(client, 124, p["id"], 3)
    d = (await client.get(f"/api/goals/{gid}", headers=auth(OWNER_TG))).json()
    first = d["rows"][0]
    assert (
        first["full_name"] == "Быстрая Анна"
        and first["place"] == 1
        and first["prize"] == "Сертификат"
    )
    assert d["rows"][1]["place"] == 2 and d["rows"][1]["prize"] is None

    # Максимум за период, ничья с общим местом.
    r = await client.post(
        "/api/goals",
        json=_goal(
            kind="competition",
            mode="max",
            target_units=None,
            product_ids=[p["id"]],
            participants_spec={"type": "users", "ids": [a, b]},
            tie_break="shared",
            counting="completed",
        ),
        headers=auth(OWNER_TG),
    )
    await _approve(client, 123, p["id"], 1)
    d = (await client.get(f"/api/goals/{r.json()['id']}", headers=auth(OWNER_TG))).json()
    assert [x["place"] for x in d["rows"]] == [1, 1]


async def test_team_goal_and_frozen_participants(client):
    await owner_ready(client)
    net = (await client.post("/api/networks", json={"name": "Юг"}, headers=auth(OWNER_TG))).json()
    a = await make_active(client, 125, "Анна", "А")
    b = await make_active(client, 126, "Борис", "Б")
    for uid in (a, b):
        await client.put(
            f"/api/users/{uid}/networks", json={"network_ids": [net["id"]]}, headers=auth(OWNER_TG)
        )
    p = await ready_product(client, [a, b], n_tasks=6, active_task_limit=6)
    r = await client.post(
        "/api/goals",
        json=_goal(
            scope="team",
            ranking="min_each",
            target_units=2,
            product_ids=[p["id"]],
            participants_spec={"type": "network", "ids": [net["id"]]},
            visibility="all",
        ),
        headers=auth(OWNER_TG),
    )
    gid = r.json()["id"]
    late = await make_active(client, 127, "Вера", "Поздняя")
    await client.put(
        f"/api/users/{late}/networks", json={"network_ids": [net["id"]]}, headers=auth(OWNER_TG)
    )
    await _approve(client, 125, p["id"], 3)
    await _approve(client, 126, p["id"], 1)
    d = (await client.get(f"/api/goals/{gid}", headers=auth(OWNER_TG))).json()
    assert d["aggregate"]["participants"] == 2  # состав зафиксирован на старте
    assert d["aggregate"]["min_units"] == 1 and d["aggregate"]["team_done"] is False


async def _visitor(tg_id: int, code: str) -> User:
    async with get_sessionmaker()() as s:
        u = await users.upsert_from_telegram(
            s, TelegramUser(tg_id, "Гость", None, None), started_bot=True
        )
        await s.commit()
        await candidates.record_visit(s, u, code)
        return u


async def _fill(client, tg):
    r = await client.put(
        "/api/my/candidate",
        json={
            "first_name": "Ольга",
            "last_name": "Новикова",
            "location": "м. Сокол",
            "days_per_week": 4,
            "can_train": True,
            "truthful": True,
            "pd_consent": True,
            "skills": {"Общение с людьми": 8, "Внимательность": 9, "Работа со смартфоном": 10},
        },
        headers=auth(tg),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_candidate_full_flow_with_referral_and_training(client, session):
    await owner_ready(client)
    referrer = await make_active(client, 130, "Рита", "Рефералова")
    await grant(client, referrer, "referrals.use")
    await client.patch("/api/settings", json={"referral_bonus_kop": 50000}, headers=auth(OWNER_TG))
    ref = (await client.get("/api/my/referral", headers=auth(130))).json()
    assert ref["link"] == f"https://t.me/wakeup_test_bot?start=ref_{ref['code']}"
    assert (await client.get("/api/my/referral/qr.svg", headers=auth(130))).text.startswith("<?xml")

    await _visitor(131, ref["code"])
    await _visitor(131, ref["code"])  # повторный переход того же человека не считается
    me = (await client.get("/api/me", headers=auth(131))).json()
    assert me["screen"] == "candidate_form"

    r = await client.put("/api/my/candidate", json={"first_name": "Ольга"}, headers=auth(131))
    assert r.json()["referred"] is True
    r = await client.post("/api/my/candidate/submit", headers=auth(131))
    assert r.status_code == 400 and "Заполните" in r.json()["message"]
    await _fill(client, 131)
    r = await client.post("/api/my/candidate/submit", headers=auth(131))
    assert r.json()["candidate"]["status"] == "submitted"
    assert r.json()["candidate"]["pd_consent_version"] == "1.0"
    assert (await client.get("/api/me", headers=auth(131))).json()["screen"] == "candidate"
    cid = r.json()["candidate"]["id"]

    # Рассмотрение: уточнение → исправление → одобрение.
    h = auth(OWNER_TG)
    await client.post(f"/api/candidates/{cid}/review/take", json={}, headers=h)
    r = await client.post(
        f"/api/candidates/{cid}/review/request_info", json={"text": "Уточните метро"}, headers=h
    )
    assert r.json()["status"] == "needs_info"
    await client.put("/api/my/candidate", json={"location": "м. Аэропорт"}, headers=auth(131))
    r = await client.post("/api/my/candidate/submit", headers=auth(131))
    assert r.json()["candidate"]["status"] == "submitted"
    assert (await client.post(f"/api/candidates/{cid}/review/approve", json={}, headers=h)).json()[
        "status"
    ] == "approved"

    # Ответственный и шаблон сообщения: статус доставки — из очереди Telegram, а не «успех».
    resp = await make_active(client, 132, "Иван", "Ответственный", role="instructor")
    r = await client.put(
        f"/api/candidates/{cid}/responsible", json={"responsible_id": resp}, headers=h
    )
    key = r.json()["message_key"]
    msg = await session.scalar(select(TelegramOutbox).where(TelegramOutbox.dedup_key == key))
    assert "Ответственный Иван" in msg.payload["text"]
    det = (await client.get(f"/api/candidates/{cid}", headers=h)).json()
    assert det["template_status"] == "pending"

    r = await client.post("/api/my/candidate/contact", json={"status": "wrote"}, headers=auth(131))
    assert r.json()["candidate"]["contact_status"] == "wrote"

    # Обучение: назначить, подтвердить, отменить подтверждённое — только с повторным подтверждением.
    starts = (timeutil.now_utc() + timedelta(days=1)).isoformat()
    r = await client.post(
        f"/api/candidates/{cid}/trainings", json={"starts_at": starts, "place": "Офис"}, headers=h
    )
    assert r.status_code == 201, r.text
    t = r.json()
    await client.post(f"/api/my/trainings/{t['id']}/confirm", headers=auth(131))
    r = await client.post(f"/api/trainings/{t['id']}/cancel", json={"reason": "Перенос"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "confirm_required"
    r = await client.post(
        f"/api/trainings/{t['id']}/cancel", json={"reason": "Перенос", "force": True}, headers=h
    )
    assert r.json()["status"] == "cancelled"

    r = await client.post(f"/api/candidates/{cid}/start-training", headers=h)
    assert r.json()["status"] == "active" and r.json()["training_status"] == "training"
    me = (await client.get("/api/me", headers=auth(131))).json()
    assert me["screen"] == "ready" and me["user"]["full_name"] == "Новикова Ольга"
    trainee = me["user"]["id"]

    # Учебные единицы: фактические, подтверждённые, учитываемые в оплате.
    p = await ready_product(client, [trainee], n_tasks=3, active_task_limit=3, training_volume=3)
    await _approve(client, 131, p["id"], 2)
    t3 = await take(client, 131, p["id"])
    await run_task(client, 131, t3["id"])  # третья — на модерации
    prog = (await client.get("/api/my/training", headers=auth(131))).json()["progress"][0]
    assert (prog["actual_units"], prog["confirmed_units"], prog["paid_units"], prog["state"]) == (
        3,
        0,
        2,
        "awaiting_confirmation",
    )
    # Подтверждена только часть объёма.
    r = await client.post(
        f"/api/trainees/{trainee}/confirm-units",
        json={"product_id": p["id"], "confirmed_units": 2},
        headers=h,
    )
    assert r.json()["training_status"] == "training"
    r = await client.post(
        f"/api/trainees/{trainee}/confirm-units",
        json={"product_id": p["id"], "confirmed_units": 3},
        headers=h,
    )
    assert r.json()["training_status"] == "completed"
    bonus = await session.scalar(
        select(LedgerEntry).where(LedgerEntry.kind == EntryKind.REFERRAL_BONUS)
    )
    assert bonus.user_id == referrer and bonus.amount_kop == 50000

    # Подтверждённый объём уменьшили — бонус не удаляется, а корректируется отдельной записью.
    r = await client.post(
        f"/api/trainees/{trainee}/confirm-units",
        json={"product_id": p["id"], "confirmed_units": 1},
        headers=h,
    )
    assert r.json()["training_status"] == "training"
    kinds = (
        (await session.execute(select(LedgerEntry.kind).where(LedgerEntry.user_id == referrer)))
        .scalars()
        .all()
    )
    assert sorted(kinds) == sorted([EntryKind.REFERRAL_BONUS, EntryKind.BONUS_REVERSAL])

    stats = (await client.get("/api/my/referral", headers=auth(130))).json()
    assert stats["visits"] == 1 and stats["submitted"] == 1 and stats["approved"] == 1
    assert stats["training_done"] == 1 and stats["bonus_kop"] == 0


async def test_candidate_review_permissions(client):
    await owner_ready(client)
    await make_active(client, 133, role="admin")
    assert (await client.get("/api/candidates", headers=auth(133))).status_code == 403
    assert (await client.get("/api/my/referral", headers=auth(133))).status_code == 403


async def test_goal_counting_completed_approved_paid(client):
    """Цель может считать все сделанные, допущенные к оплате или только оплаченные единицы."""
    await owner_ready(client)
    a = await make_active(client, 140, "Вера", "Счётная")
    p = await ready_product(client, [a], n_tasks=3, active_task_limit=3)
    gids = {}
    for mode in ("completed", "approved", "paid"):
        r = await client.post(
            "/api/goals",
            json=_goal(
                participants_spec={"type": "users", "ids": [a]},
                product_ids=[p["id"]],
                counting=mode,
                visibility="all",
            ),
            headers=auth(OWNER_TG),
        )
        assert r.status_code == 201, r.text
        gids[mode] = r.json()["id"]
    await _approve(client, 140, p["id"], 2)
    t = await take(client, 140, p["id"])
    await run_task(client, 140, t["id"])  # третье — на модерации

    async def units():
        out = {}
        for mode, gid in gids.items():
            d = (await client.get(f"/api/goals/{gid}", headers=auth(OWNER_TG))).json()
            out[mode] = d["aggregate"]["total_units"]
        return out

    assert await units() == {"completed": 3, "approved": 2, "paid": 0}
    r = await client.post(
        f"/api/users/{a}/payouts",
        json={"start": str(TODAY), "end": str(TODAY), "idempotency_key": "goal-paid-1"},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 201, r.text
    assert await units() == {"completed": 3, "approved": 2, "paid": 2}
