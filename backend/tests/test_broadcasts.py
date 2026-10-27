"""Этап 12: рассылки и Telegram-архив аудита."""

from datetime import timedelta

from sqlalchemy import func, select

from app import timeutil
from app.db import get_sessionmaker
from app.models import AuditEvent, BroadcastRecipient, TelegramOutbox
from app.security.telegram_auth import TelegramUser
from app.services import audit_archive, broadcasts, users
from app.telegram_api import TelegramError
from bot.outbox_worker import process_due
from tests.helpers import OWNER_TG, auth, grant, make_active, owner_ready

DAY = timedelta(days=1)


async def _bot_start(*tg_ids):
    async with get_sessionmaker()() as s:
        for t in tg_ids:
            await users.upsert_from_telegram(s, TelegramUser(t, "X", None, None), started_bot=True)
        await s.commit()


def test_sanitize_keeps_only_telegram_tags():
    html = (
        "Привет <b>всем</b>! <script>alert(1)</script>"
        '<a href="javascript:x">ссылка</a> <a href="https://a.ru">ок</a> 5 < 6'
    )
    clean = broadcasts.sanitize(html)
    assert "<b>всем</b>" in clean and "<script>" not in clean and "javascript" not in clean
    assert '<a href="https://a.ru">ок</a>' in clean and "5 &lt; 6" in clean


async def test_audience_filters_and_send(client, gateway, session):
    await owner_ready(client)
    a = await make_active(client, 170, "Анна", "Завтрашняя")
    b = await make_active(client, 171, "Борис", "Сегодняшний")
    c = await make_active(client, 172, "Вера", "Безстатусная")
    await _bot_start(170, 171)
    today = timeutil.msk_today()
    pts = (
        await client.post("/api/work-points", json={"name": "Точка"}, headers=auth(OWNER_TG))
    ).json()
    for uid, day in ((a, today + DAY), (b, today)):
        r = await client.post(
            "/api/shifts",
            json={
                "day": str(day),
                "start": "10:00",
                "end": "18:00",
                "point_id": pts["id"],
                "user_id": uid,
            },
            headers=auth(OWNER_TG),
        )
        assert r.status_code == 201, r.text

    async def count(aud):
        bid = (
            await client.post(
                "/api/broadcasts", json={"text": "Тест", "audience": aud}, headers=auth(OWNER_TG)
            )
        ).json()["id"]
        pv = (await client.get(f"/api/broadcasts/{bid}/preview", headers=auth(OWNER_TG))).json()
        return bid, {r["user_id"] for r in pv["recipients"]}

    _, s = await count({"scheduled_tomorrow": True})
    assert s == {a}
    _, s = await count({"working_today": True})
    assert s == {b}
    _, s = await count({"no_status_tomorrow": True, "user_ids": [a, b, c]})
    assert s == {b, c}  # комбинация фильтров — пересечение
    r = await client.post(
        "/api/broadcasts", json={"text": "x", "audience": {}}, headers=auth(OWNER_TG)
    )
    bid = r.json()["id"]
    assert (await client.get(f"/api/broadcasts/{bid}/preview", headers=auth(OWNER_TG))).json()[
        "code"
    ] == "no_audience"

    bid, s = await count({"user_ids": [a, b, c]})
    await client.put(
        f"/api/broadcasts/{bid}",
        json={
            "text": "<b>Важно</b>: собрание",
            "audience": {"user_ids": [a, b, c]},
            "require_ack": True,
            "allow_replies": True,
        },
        headers=auth(OWNER_TG),
    )
    r = await client.post(
        f"/api/broadcasts/{bid}/send", json={"expected_count": 2}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 409 and r.json()["code"] == "changed"
    r = await client.post(
        f"/api/broadcasts/{bid}/send", json={"expected_count": 3}, headers=auth(OWNER_TG)
    )
    assert r.json()["status"] == "sent"
    again = await client.post(
        f"/api/broadcasts/{bid}/send", json={"expected_count": 3}, headers=auth(OWNER_TG)
    )
    assert again.json()["status"] == "sent"
    assert (
        await session.scalar(
            select(func.count()).where(TelegramOutbox.dedup_key.like(f"broadcast:{bid}:%"))
        )
        == 2
    )

    await process_due(get_sessionmaker(), gateway.send)
    sent = [x for x in gateway.sent if x[2].get("text", "").startswith("<b>Важно</b>")]
    assert (
        len(sent) == 2
        and sent[0][2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"ack:{bid}"
    )

    d = (await client.get(f"/api/broadcasts/{bid}", headers=auth(OWNER_TG))).json()
    assert d["counts"] == {"queued": 0, "sent": 2, "failed": 1, "acked": 0, "total": 3}
    failed = next(r for r in d["recipients"] if r["status"] == "failed")
    assert "не запускал бота" in failed["error"]  # ошибка доставки не выдаётся за успех

    # «Я ознакомился» — из приложения и кнопкой в боте.
    assert (await client.post(f"/api/my/broadcasts/{bid}/ack", headers=auth(170))).json() == {
        "ok": True
    }
    async with get_sessionmaker()() as s:
        user_b = await users.upsert_from_telegram(s, TelegramUser(171, "X", None, None))
        await s.commit()
        assert await broadcasts.acknowledge(s, user_b, bid)
        rec = await s.scalar(select(BroadcastRecipient).where(BroadcastRecipient.user_id == b))
        assert await broadcasts.save_reply(s, user_b, rec.tg_message_id, "Буду", None, None)
    d = (await client.get(f"/api/broadcasts/{bid}", headers=auth(OWNER_TG))).json()
    assert d["counts"]["acked"] == 2 and d["replies"][0]["text"] == "Буду"
    mine = (await client.get("/api/my/broadcasts", headers=auth(170))).json()
    assert mine[0]["acked_at"] is not None


async def test_broadcast_permissions_and_delivery_failure(client, gateway):
    await owner_ready(client)
    admin = await make_active(client, 173, role="admin")
    emp = await make_active(client, 174)
    await _bot_start(174)
    r = await client.post(
        "/api/broadcasts", json={"text": "x", "audience": {"all": True}}, headers=auth(173)
    )
    assert r.status_code == 403
    await grant(client, admin, "broadcasts.send")
    bid = (
        await client.post(
            "/api/broadcasts",
            json={"text": "x", "audience": {"user_ids": [emp]}},
            headers=auth(173),
        )
    ).json()["id"]
    await client.post(f"/api/broadcasts/{bid}/send", json={"expected_count": 1}, headers=auth(173))
    gateway.fail_send = TelegramError(
        "Forbidden: bot was blocked by the user", permanent=True, bot_blocked=True
    )
    await process_due(get_sessionmaker(), gateway.send)
    d = (await client.get(f"/api/broadcasts/{bid}", headers=auth(173))).json()
    assert d["counts"]["failed"] == 1 and d["counts"]["sent"] == 0


async def test_audit_archive(client, gateway, session):
    await owner_ready(client)
    h = auth(OWNER_TG)
    g = (await client.post("/api/groups", json={"chat_id": -3001}, headers=h)).json()
    assert (await client.post(f"/api/groups/{g['id']}/audit-topics", headers=h)).status_code == 409
    await client.patch(f"/api/groups/{g['id']}", json={"purpose": "audit"}, headers=h)
    r = (await client.post(f"/api/groups/{g['id']}/audit-topics", headers=h)).json()
    assert len(r["topics"]) == len(audit_archive.TOPICS)
    # Старые события не выгружаются лавиной: архив начинается с момента подключения.
    async with get_sessionmaker()() as s:
        assert await audit_archive.export_batch(s) == 0
    emp = await make_active(client, 175, "Глеб", "Архивный")
    await client.put(
        f"/api/users/{emp}/status", json={"status": "blocked", "reason": "Тест"}, headers=h
    )
    async with get_sessionmaker()() as s:
        n = await audit_archive.export_batch(s)
    assert n >= 3
    async with get_sessionmaker()() as s:
        assert await audit_archive.export_batch(s) == 0  # курсор сдвинулся, дублей нет
    msgs = (
        (
            await session.execute(
                select(TelegramOutbox).where(TelegramOutbox.dedup_key.like("audit_archive:%"))
            )
        )
        .scalars()
        .all()
    )
    blocked = next(m for m in msgs if "status.changed" in m.payload["text"])
    assert blocked.payload["message_thread_id"] == r["topics"]["users"]
    for part in (
        "Пользователи и доступы",
        "Инициатор: Владельцев Олег",
        "Сотрудник: Архивный Глеб",
        "Причина: Тест",
        "МСК",
    ):
        assert part in blocked.payload["text"]

    # Telegram недоступен: записи остаются в БД, а ошибки архива не порождают новые записи аудита.
    before = await session.scalar(select(func.count()).select_from(AuditEvent))
    gateway.fail_send = TelegramError("Bad Request: chat not found", permanent=True)
    await process_due(get_sessionmaker(), gateway.send)
    after = await session.scalar(select(func.count()).select_from(AuditEvent))
    archive_failures = await session.scalar(
        select(func.count()).where(
            AuditEvent.action == "telegram.delivery_failed",
            AuditEvent.object_id.in_([str(m.id) for m in msgs]),
        )
    )
    assert archive_failures == 0 and after >= before


async def test_audit_log_view(client):
    await owner_ready(client)
    admin = await make_active(client, 176, role="admin")
    assert (await client.get("/api/audit", headers=auth(176))).status_code == 403
    await grant(client, admin, "audit.view")
    rows = (await client.get("/api/audit?category=users", headers=auth(176))).json()
    assert rows and all(r["category"] == "users" for r in rows)
