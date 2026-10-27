"""Этап 11: фотоотчёты в Telegram, заголовки и сводки дня."""

from datetime import datetime, timedelta

from sqlalchemy import select, update

from app import jobs, timeutil
from app.db import get_sessionmaker
from app.models import DayPost, PhotoDelivery, Task, TaskPhoto, TaskStatus, TelegramOutbox
from app.services import photoreports
from app.telegram_api import TelegramError
from bot.outbox_worker import process_due
from tests.helpers import OWNER_TG, auth, make_active, owner_ready, photo, ready_product, take

PRODUCT_CHAT, GENERAL_CHAT, SUMMARY_CHAT = -2001, -2002, -2003


async def _setup(client, *, product_route=True):
    await owner_ready(client)
    emp = await make_active(client, 160, "Иван", "Иванов")
    p = await ready_product(
        client, [emp], n_tasks=3, active_task_limit=3, final_photo_required=True
    )
    h = auth(OWNER_TG)
    gids = {}
    for chat in (PRODUCT_CHAT, GENERAL_CHAT, SUMMARY_CHAT):
        gids[chat] = (await client.post("/api/groups", json={"chat_id": chat}, headers=h)).json()[
            "id"
        ]
    if product_route:
        await client.post(
            "/api/photoreports/routes",
            json={"kind": "product", "product_id": p["id"], "group_id": gids[PRODUCT_CHAT]},
            headers=h,
        )
    await client.post(
        "/api/photoreports/routes",
        json={"kind": "general", "group_id": gids[GENERAL_CHAT]},
        headers=h,
    )
    await client.post(
        "/api/photoreports/routes",
        json={"kind": "summary", "group_id": gids[SUMMARY_CHAT]},
        headers=h,
    )
    return emp, p, gids


async def _final_photo(client, product_id):
    t = await take(client, 160, product_id)
    hh = auth(160)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=hh)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=hh)
    r = await photo(client, 160, t["id"])
    assert r.status_code == 200, r.text
    return t, r.json()["uploaded"]


async def test_photo_routed_to_product_and_general_groups(client, gateway, session):
    emp, p, gids = await _setup(client)
    t, ph = await _final_photo(client, p["id"])
    await process_due(get_sessionmaker(), gateway.send)
    photos = [x for x in gateway.sent if x[0] == "sendPhoto"]
    assert sorted(x[1] for x in photos) == sorted([PRODUCT_CHAT, GENERAL_CHAT])
    cap = photos[0][2]["caption"]
    for part in (
        "Фотоотчёт — Продукт А",
        "МСК",
        "Сотрудник: Иванов Иван",
        f"ID задания: {t['external_id']}",
        "Единиц: 1",
        "Этап: Финальный фотоотчёт",
        "Статус: Выполняется",
    ):
        assert part in cap
    deliveries = (await session.execute(select(PhotoDelivery))).scalars().all()
    assert {d.status for d in deliveries} == {"sent"} and all(d.tg_message_id for d in deliveries)
    assert SUMMARY_CHAT not in [x[1] for x in photos]  # в группу сводки фото не идут


async def test_missing_route_keeps_photo_and_alerts_owner(client, gateway, session):
    emp, p, gids = await _setup(client, product_route=False)
    await _final_photo(client, p["id"])
    await _final_photo(client, p["id"])
    alerts = (
        (
            await session.execute(
                select(TelegramOutbox).where(TelegramOutbox.dedup_key.like("photo_route_missing:%"))
            )
        )
        .scalars()
        .all()
    )
    assert len(alerts) == 1 and alerts[0].chat_id == OWNER_TG  # одна ошибка конфигурации в день
    assert len((await session.execute(select(TaskPhoto))).scalars().all()) == 2
    # Общая группа получает фото; в случайную группу продукта — нет.
    await process_due(get_sessionmaker(), gateway.send)
    assert {x[1] for x in gateway.sent if x[0] == "sendPhoto"} == {GENERAL_CHAT}


async def test_telegram_failure_keeps_photo_and_retry(client, gateway, session):
    emp, p, gids = await _setup(client)
    await _final_photo(client, p["id"])
    gateway.fail_send = TelegramError("Forbidden: bot is not a member", permanent=True)
    await process_due(get_sessionmaker(), gateway.send)
    failed = (
        (await session.execute(select(PhotoDelivery).where(PhotoDelivery.status == "failed")))
        .scalars()
        .all()
    )
    assert len(failed) == 2
    assert (await session.execute(select(TaskPhoto))).scalars().first() is not None  # фото на месте
    alert = await session.scalar(
        select(TelegramOutbox).where(TelegramOutbox.dedup_key.like("photo_delivery_failed:%"))
    )
    assert alert is not None

    gateway.fail_send = None
    r = await client.post(
        f"/api/photoreports/deliveries/{failed[0].id}/retry", headers=auth(OWNER_TG)
    )
    assert r.json()["status"] == "queued"
    await process_due(get_sessionmaker(), gateway.send)
    await session.refresh(failed[0])
    assert failed[0].status == "sent"


async def test_one_header_per_day_and_replies(client, gateway, session):
    emp, p, gids = await _setup(client)
    today = timeutil.msk_today()
    async with get_sessionmaker()() as s:
        assert await photoreports.post_headers(s, today) == 2  # группа продукта и общая
    async with get_sessionmaker()() as s:
        assert await photoreports.post_headers(s, today) == 0  # повторный запуск — без дублей
    await process_due(get_sessionmaker(), gateway.send)
    headers = [x for x in gateway.sent if x[0] == "sendMessage" and "Фотоотчёты за" in x[2]["text"]]
    assert len(headers) == 2 and photoreports.day_title(today) in headers[0][2]["text"]
    await _final_photo(client, p["id"])
    await process_due(get_sessionmaker(), gateway.send)
    photos = [x for x in gateway.sent if x[0] == "sendPhoto"]
    assert all(x[2].get("reply_to_message_id") for x in photos)


async def test_zero_summary_and_corrections(client, gateway, session):
    emp, p, gids = await _setup(client)
    day = timeutil.msk_today() - timedelta(days=1)
    async with get_sessionmaker()() as s:
        assert await photoreports.post_summaries(s, day) == 1
    async with get_sessionmaker()() as s:
        assert await photoreports.post_summaries(s, day) == 0
    await process_due(get_sessionmaker(), gateway.send)
    text = next(x[2]["text"] for x in gateway.sent if x[1] == SUMMARY_CHAT)
    assert f"Итоги фотоотчётов — {photoreports.day_title(day)}" in text
    assert "Фотоотчётов: 0" in text and "Ошибок отправки: 0" in text

    # Поздний отчёт: задание, завершённое вчера, допущено к оплате сегодня → корректировка.
    async with get_sessionmaker()() as s:
        at = timeutil.msk_at(day) + timedelta(hours=15)
        s.add(
            Task(
                product_id=p["id"],
                external_id="late",
                status=TaskStatus.APPROVED,
                assignee_id=emp,
                found_at=at,
                completed_at=at + timedelta(minutes=5),
                units=2,
            )
        )
        await s.commit()
    async with get_sessionmaker()() as s:
        assert await photoreports.post_corrections(s, timeutil.msk_today()) == 1
    async with get_sessionmaker()() as s:
        assert await photoreports.post_corrections(s, timeutil.msk_today()) == 0
    versions = (
        (
            await session.execute(
                select(DayPost).where(DayPost.kind == "summary").order_by(DayPost.version)
            )
        )
        .scalars()
        .all()
    )
    assert [v.version for v in versions] == [1, 2]
    assert (
        versions[0].content["units"] == 0 and versions[1].content["approved"] == 2
    )  # история сохранена
    await process_due(get_sessionmaker(), gateway.send)
    corr = [x for x in gateway.sent if "Корректировка итогов" in x[2].get("text", "")]
    assert len(corr) == 1 and "Выполненных единиц: 0 → 2" in corr[0][2]["text"]


async def test_scheduler_posts_summary_then_header(client, gateway):
    await _setup(client)
    now = datetime.combine(
        timeutil.msk_today(), datetime.min.time().replace(minute=6), tzinfo=timeutil.MSK
    )
    ran = await jobs.run_due(get_sessionmaker(), now)
    names = [r.split(":")[0] for r in ran]
    assert names.index("photo_summary") < names.index("photo_header")
    assert await jobs.run_due(get_sessionmaker(), now) == []


async def test_status_change_does_not_resend_photo(client, gateway, session):
    emp, p, gids = await _setup(client)
    t, ph = await _final_photo(client, p["id"])
    async with get_sessionmaker()() as s:
        await s.execute(update(Task).where(Task.id == t["id"]).values(status=TaskStatus.MODERATION))
        await s.commit()
    await process_due(get_sessionmaker(), gateway.send)
    assert len([x for x in gateway.sent if x[0] == "sendPhoto"]) == 2
