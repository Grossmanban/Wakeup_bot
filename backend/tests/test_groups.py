"""Этап 10: группы, членство, новички, приглашения."""

from datetime import timedelta

from sqlalchemy import select, update

from app import timeutil
from app.db import get_sessionmaker
from app.models import GroupInvite, Task, TaskStatus
from app.security.telegram_auth import TelegramUser
from app.services import groups, users
from app.telegram_api import TelegramError
from bot.outbox_worker import process_due
from tests.helpers import OWNER_TG, auth, grant, make_active, owner_ready, ready_product

TRAINING_CHAT, MAIN_CHAT = -1001, -1002


async def _groups(client):
    h = auth(OWNER_TG)
    t = (await client.post("/api/groups", json={"chat_id": TRAINING_CHAT}, headers=h)).json()
    m = (await client.post("/api/groups", json={"chat_id": MAIN_CHAT}, headers=h)).json()
    assert (
        await client.patch(f"/api/groups/{t['id']}", json={"purpose": "training"}, headers=h)
    ).status_code == 200
    assert (
        await client.patch(f"/api/groups/{m['id']}", json={"purpose": "main"}, headers=h)
    ).status_code == 200
    return t, m


async def _work_days(user_id: int, product_id: int, n: int):
    """n разных дней по МСК с выполненными заданиями (в один из дней — два задания)."""
    async with get_sessionmaker()() as s:
        for i in range(n):
            day = timeutil.msk_today() - timedelta(days=10 - i)
            at = timeutil.msk_at(day) + timedelta(hours=12)
            for k in range(2 if i == 0 else 1):
                s.add(
                    Task(
                        product_id=product_id,
                        external_id=f"W{user_id}-{i}-{k}",
                        status=TaskStatus.APPROVED,
                        assignee_id=user_id,
                        found_at=at,
                        completed_at=at + timedelta(minutes=10),
                        units=1,
                    )
                )
        await s.commit()


async def _bot_start(tg_id):
    async with get_sessionmaker()() as s:
        await users.upsert_from_telegram(s, TelegramUser(tg_id, "X", None, None), started_bot=True)
        await s.commit()


async def test_group_setup_and_rights(client, gateway):
    await owner_ready(client)
    t, m = await _groups(client)
    assert t["bot_rights"]["can_invite_users"] is True and t["status"] == "connected"
    r = await client.patch(
        f"/api/groups/{m['id']}", json={"purpose": "training"}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 409  # группа стажировки может быть только одна
    gateway.rights = {"status": "left", "can_invite_users": False}
    g = (await client.post(f"/api/groups/{t['id']}/check-rights", headers=auth(OWNER_TG))).json()
    assert g["status"] == "left"
    res = (await client.post(f"/api/groups/{m['id']}/test", headers=auth(OWNER_TG))).json()
    assert res["ok"] is True
    gateway.fail_send = TelegramError("Forbidden: bot was kicked", permanent=True)
    res = (await client.post(f"/api/groups/{m['id']}/test", headers=auth(OWNER_TG))).json()
    assert res == {"ok": False, "error": "Forbidden: bot was kicked"}


async def test_newbie_progress_and_membership_rules(client, gateway):
    await owner_ready(client)
    emp = await make_active(client, 150)
    t, m = await _groups(client)
    p = await ready_product(client, [emp], n_tasks=0)
    await _work_days(emp, p["id"], 2)

    ov = (await client.get("/api/groups/overview", headers=auth(OWNER_TG))).json()
    row = next(r for r in ov["people"] if r["user_id"] == emp)
    assert row["progress"]["label"] == "2/3" and row["progress"]["units"] == 3
    # Членство ещё не проверялось — это «не удалось проверить», а не «отсутствует».
    assert row["memberships"][str(t["id"])]["status"] == "unknown"
    assert row["recommendations"] == ["Группа стажировки: не удалось проверить"]

    r = await client.post(f"/api/groups/{t['id']}/check-members", json={}, headers=auth(OWNER_TG))
    assert r.json() == {"member": 0, "not_member": 1, "unknown": 0}
    ov = (await client.get("/api/groups/overview", headers=auth(OWNER_TG))).json()
    row = next(r for r in ov["people"] if r["user_id"] == emp)
    assert row["recommendations"] == ["Необходимо добавить в группу стажировки"]

    # Третий рабочий день → 3/3, переход фиксируется, проверяется основная группа.
    async with get_sessionmaker()() as s:
        at = timeutil.msk_at(timeutil.msk_today() - timedelta(days=1)) + timedelta(hours=12)
        s.add(
            Task(
                product_id=p["id"],
                external_id="third",
                status=TaskStatus.MODERATION,
                assignee_id=emp,
                found_at=at,
                completed_at=at + timedelta(minutes=5),
                units=1,
            )
        )
        await s.commit()
    gateway.members[(TRAINING_CHAT, 150)] = "member"
    gateway.unknown.add((MAIN_CHAT, 150))
    await client.post(f"/api/groups/{t['id']}/check-members", json={}, headers=auth(OWNER_TG))
    r = await client.post(f"/api/groups/{m['id']}/check-members", json={}, headers=auth(OWNER_TG))
    assert r.json()["unknown"] == 1
    ov = (await client.get("/api/groups/overview", headers=auth(OWNER_TG))).json()
    row = next(r for r in ov["people"] if r["user_id"] == emp)
    assert row["progress"]["label"] == "3/3" and row["progress"]["is_regular"]
    assert row["became_regular_on"] == str(timeutil.msk_today() - timedelta(days=1))
    assert row["recommendations"] == [
        "Необходимо убрать из группы стажировки",
        "Основная группа: не удалось проверить",
    ]
    gateway.unknown.clear()
    await client.post(f"/api/groups/{m['id']}/check-members", json={}, headers=auth(OWNER_TG))
    ov = (await client.get("/api/groups/overview", headers=auth(OWNER_TG))).json()
    row = next(r for r in ov["people"] if r["user_id"] == emp)
    assert "Необходимо добавить в основную группу" in row["recommendations"]

    # Отсутствие активности потом не возвращает в новички; исправление истории — пересчитывает.
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Task).where(Task.external_id == "third").values(status=TaskStatus.CANCELLED)
        )
        await s.commit()
    async with get_sessionmaker()() as s:
        assert await groups.recalc_all(s) == 1


async def test_invite_sent_after_telegram_confirms(client, gateway, session):
    await owner_ready(client)
    emp = await make_active(client, 151)
    await _bot_start(151)
    t, m = await _groups(client)
    inv = (
        await client.post(
            f"/api/groups/{m['id']}/invite", json={"user_id": emp}, headers=auth(OWNER_TG)
        )
    ).json()
    assert inv["status"] == "prepared" and inv["invite_link"].startswith("https://t.me/+")
    # Повтор раньше интервала — не спамим.
    r = await client.post(
        f"/api/groups/{m['id']}/invite", json={"user_id": emp}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 409 and r.json()["code"] == "too_soon"

    await process_due(get_sessionmaker(), gateway.send)
    sent = [x for x in gateway.sent if x[1] == 151 and "Приглашение" in x[2]["text"]]
    assert (
        len(sent) == 1
        and inv["invite_link"] in sent[0][2]["text"]
        and "Группа -1002" in sent[0][2]["text"]
    )
    got = (await client.get(f"/api/groups/invites/{inv['id']}", headers=auth(OWNER_TG))).json()
    assert got["status"] == "sent"

    # Вступление по событию Telegram.
    async with get_sessionmaker()() as s:
        await groups.on_member_event(s, MAIN_CHAT, 151, "member")
    got = (await client.get(f"/api/groups/invites/{inv['id']}", headers=auth(OWNER_TG))).json()
    assert got["status"] == "joined"


async def test_invite_failures_are_not_success(client, gateway):
    await owner_ready(client)
    no_bot = await make_active(client, 152)  # не запускал бота
    blocked = await make_active(client, 153)
    await _bot_start(153)
    t, m = await _groups(client)

    r = (
        await client.post(
            f"/api/groups/{t['id']}/invite", json={"user_id": no_bot}, headers=auth(OWNER_TG)
        )
    ).json()
    assert r["status"] == "send_failed" and "передайте ссылку вручную" in r["error"]
    assert r["invite_link"]  # ссылку можно передать вручную

    r = (
        await client.post(
            f"/api/groups/{t['id']}/invite", json={"user_id": blocked}, headers=auth(OWNER_TG)
        )
    ).json()
    gateway.fail_send = TelegramError(
        "Forbidden: bot was blocked by the user", permanent=True, bot_blocked=True
    )
    await process_due(get_sessionmaker(), gateway.send)
    got = (await client.get(f"/api/groups/invites/{r['id']}", headers=auth(OWNER_TG))).json()
    assert got["status"] == "send_failed" and "blocked" in got["error"]

    gateway.fail_send = None
    gateway.fail_link = TelegramError("Bad Request: not enough rights", permanent=True)
    other = await make_active(client, 154)
    r = (
        await client.post(
            f"/api/groups/{m['id']}/invite", json={"user_id": other}, headers=auth(OWNER_TG)
        )
    ).json()
    assert r["status"] == "send_failed" and "Не удалось создать ссылку" in r["error"]


async def test_bulk_invite_and_permissions(client, gateway, session):
    await owner_ready(client)
    a = await make_active(client, 155)
    b = await make_active(client, 156)
    await _bot_start(155)
    admin = await make_active(client, 157, role="admin")
    t, m = await _groups(client)
    r = await client.post(
        f"/api/groups/{m['id']}/invite-bulk", json={"user_ids": [a, b]}, headers=auth(157)
    )
    assert r.status_code == 200 and {x["status"] for x in r.json()} == {"error"}
    await grant(client, admin, "groups.invite", "groups.view")
    res = (
        await client.post(
            f"/api/groups/{m['id']}/invite-bulk", json={"user_ids": [a, b]}, headers=auth(157)
        )
    ).json()
    by = {x["user_id"]: x for x in res}
    assert by[a]["status"] == "prepared" and by[b]["status"] == "send_failed"
    n = len((await session.execute(select(GroupInvite))).scalars().all())
    assert n == 2
