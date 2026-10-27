"""Этапы 4–5: выполнение, таймеры, фото, OCR, ручная проверка, модерация, качество."""

from datetime import timedelta

from sqlalchemy import select, update

from app.models import EntryKind, LedgerEntry, Task, TaskPhoto
from app.services.moderation import quality
from tests.helpers import (
    OWNER_TG,
    auth,
    grant,
    make_active,
    moderate,
    owner_ready,
    photo,
    ready_product,
    run_task,
    set_role_rate,
    take,
)

FLOW = [
    {"title": "Найти карточку", "checklist": ["Открыл", "Проверил адрес"], "min_seconds": 60},
    {"title": "Фото витрины", "checklist": [], "min_seconds": 0, "photo": "required"},
]


async def _shift_stage_start(session, task_id: int, seconds: int):
    await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(stage_started_at=Task.stage_started_at - timedelta(seconds=seconds))
    )
    await session.commit()


async def test_full_flow_with_stages_timer_and_photos(client, session):
    await owner_ready(client)
    emp = await make_active(client, 50)
    p = await ready_product(client, [emp], n_tasks=1, flow=FLOW, final_photo_required=True)
    t = await take(client, 50, p["id"])
    h = auth(50)
    tid = t["id"]

    # «Найдено» до открытия ссылки — нельзя.
    r = await client.post(f"/api/my/tasks/{tid}/found", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "open_first"
    await client.post(f"/api/my/tasks/{tid}/open", headers=h)
    v = (await client.post(f"/api/my/tasks/{tid}/found", headers=h)).json()
    assert v["stage_state"] == {"stage": 0, "total": 2, "seconds_left": 60}
    found_at = v["task"]["found_at"]
    # Повторное «Найдено» не сбрасывает таймер.
    v = (await client.post(f"/api/my/tasks/{tid}/found", headers=h)).json()
    assert v["task"]["found_at"] == found_at

    # Чек-лист не отмечен.
    r = await client.post(
        f"/api/my/tasks/{tid}/stages/0/complete", json={"checklist": [True]}, headers=h
    )
    assert r.json()["code"] == "checklist"
    # Таймер не истёк — клиентское время не влияет, проверка на сервере.
    r = await client.post(
        f"/api/my/tasks/{tid}/stages/0/complete", json={"checklist": [True, True]}, headers=h
    )
    assert r.status_code == 409 and r.json()["code"] == "timer"
    await _shift_stage_start(session, tid, 61)
    r = await client.post(
        f"/api/my/tasks/{tid}/stages/0/complete", json={"checklist": [True, True]}, headers=h
    )
    assert r.status_code == 200 and r.json()["task"]["current_stage"] == 1
    # Повтор того же запроса не продвигает дальше.
    r = await client.post(
        f"/api/my/tasks/{tid}/stages/0/complete", json={"checklist": [True, True]}, headers=h
    )
    assert r.json()["task"]["current_stage"] == 1

    # Этап 2 требует фото.
    r = await client.post(
        f"/api/my/tasks/{tid}/stages/1/complete", json={"checklist": []}, headers=h
    )
    assert r.json()["code"] == "photo_required"
    r = await photo(client, 50, tid, "stage", 1)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/my/tasks/{tid}/stages/1/complete", json={"checklist": []}, headers=h
    )
    assert r.json()["task"]["current_stage"] == 2

    # Завершение без финального фото — нельзя.
    r = await client.post(f"/api/my/tasks/{tid}/complete", headers=h)
    assert r.json()["code"] == "photo_required"
    assert (await photo(client, 50, tid)).status_code == 200
    v = (await client.post(f"/api/my/tasks/{tid}/complete", headers=h)).json()
    assert v["task"]["status"] == "moderation"
    # Повторное «Завершить» ничего не ломает.
    v = (await client.post(f"/api/my/tasks/{tid}/complete", headers=h)).json()
    assert v["task"]["status"] == "moderation"

    photos = (
        (await session.execute(select(TaskPhoto).where(TaskPhoto.task_id == tid))).scalars().all()
    )
    assert [(x.kind.value, x.stage) for x in photos] == [("stage", 1), ("final", None)]

    detail = (await client.get(f"/api/tasks/{tid}", headers=auth(OWNER_TG))).json()
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds == [
        "assigned",
        "opened",
        "found",
        "stage_done",
        "photo_uploaded",
        "stage_done",
        "photo_uploaded",
        "completed",
    ]


async def test_timer_reset_requires_reason(client):
    await owner_ready(client)
    emp = await make_active(client, 51)
    p = await ready_product(client, [emp], n_tasks=1, flow=FLOW[:1])
    t = await take(client, 51, p["id"])
    h = auth(51)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=h)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=h)
    r = await client.post(f"/api/my/tasks/{t['id']}/timer-reset", json={}, headers=h)
    assert r.json()["code"] == "reason_required"
    r = await client.post(
        f"/api/my/tasks/{t['id']}/timer-reset", json={"reason": "Карточка не грузилась"}, headers=h
    )
    assert r.status_code == 200
    assert r.json()["stage_state"]["seconds_left"] == 60


async def test_photo_validation_and_access(client):
    await owner_ready(client)
    emp = await make_active(client, 52)
    other = await make_active(client, 53)
    p = await ready_product(client, [emp, other], n_tasks=2, final_photo_required=True)
    t = await take(client, 52, p["id"])
    h = auth(52)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=h)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=h)
    r = await client.post(
        f"/api/my/tasks/{t['id']}/photos",
        files={"file": ("x.txt", b"hello", "text/plain")},
        data={"kind": "final"},
        headers=h,
    )
    assert r.status_code == 400 and r.json()["code"] == "bad_photo"
    r = await photo(client, 52, t["id"])
    pid = r.json()["uploaded"]["id"]
    assert (await client.get(f"/api/photos/{pid}", headers=h)).status_code == 200
    assert (await client.get(f"/api/photos/{pid}", headers=auth(53))).status_code == 404
    assert (await client.get(f"/api/photos/{pid}", headers=auth(OWNER_TG))).status_code == 200
    # Чужое задание не видно и не меняется.
    assert (await client.get(f"/api/my/tasks/{t['id']}", headers=auth(53))).status_code == 404
    assert (await photo(client, 53, t["id"])).status_code == 404


async def test_ocr_attempts_then_manual_review(client, session, fake_ocr):
    await owner_ready(client)
    emp = await make_active(client, 54)
    p = await ready_product(
        client,
        [emp],
        n_tasks=0,
        ocr_enabled=True,
        ocr_attempts=3,
        expected_text_default="Спасибо за отзыв",
    )
    from tests.helpers import import_tasks

    await import_tasks(client, p["id"], ["O1"])
    t = await take(client, 54, p["id"])
    h = auth(54)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=h)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=h)

    fake_ocr.texts = ["что-то другое", "СПАСИБО ЗА 0ТЗЫВ", "пусто"]
    r1 = (await photo(client, 54, t["id"])).json()
    assert r1["uploaded"]["ocr_status"] == "failed"
    assert r1["uploaded"]["ocr_text"] == "что-то другое"
    assert r1["ocr_attempts_left"] == 2
    r = await client.post(f"/api/my/tasks/{t['id']}/complete", headers=h)
    assert r.json()["code"] == "ocr_not_passed"
    r2 = (await photo(client, 54, t["id"])).json()
    assert r2["uploaded"]["ocr_status"] == "failed" and r2["ocr_attempts_left"] == 1
    r3 = (await photo(client, 54, t["id"])).json()
    assert r3["task"]["status"] == "manual_review"
    assert "Исчерпаны попытки" in r3["task"]["manual_review_reason"]
    # После исчерпания попыток новые фото не принимаются.
    r = await photo(client, 54, t["id"])
    assert r.status_code == 409

    queue = (await client.get("/api/manual-review", headers=auth(OWNER_TG))).json()
    assert len(queue) == 1 and len(queue[0]["photos"]) == 3
    assert [e["kind"] for e in queue[0]["events"]].count("ocr_attempt") == 3

    # Владелец одобряет вручную — задание уходит на модерацию.
    r = await client.post(f"/api/manual-review/{t['id']}/approve", json={}, headers=auth(OWNER_TG))
    assert r.status_code == 200 and r.json()["status"] == "moderation"


async def test_ocr_pass_with_latin_lookalikes(client, fake_ocr):
    await owner_ready(client)
    emp = await make_active(client, 55)
    p = await ready_product(
        client, [emp], n_tasks=1, ocr_enabled=True, expected_text_default="Код заказа ЯНД-4821"
    )
    t = await take(client, 55, p["id"])
    h = auth(55)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=h)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=h)
    fake_ocr.texts = ["Спасибо!\nKод заказа: янд-4821"]  # латинская K
    r = (await photo(client, 55, t["id"])).json()
    assert r["uploaded"]["ocr_status"] == "passed"
    v = (await client.post(f"/api/my/tasks/{t['id']}/complete", headers=h)).json()
    assert v["task"]["status"] == "moderation"


async def test_ocr_unavailable_goes_to_manual_review(client, fake_ocr):
    from app.ocr import OcrUnavailable

    await owner_ready(client)
    emp = await make_active(client, 56)
    p = await ready_product(client, [emp], n_tasks=1, ocr_enabled=True, expected_text_default="abc")
    t = await take(client, 56, p["id"])
    h = auth(56)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=h)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=h)
    fake_ocr.texts = [OcrUnavailable("нет движка")]
    r = (await photo(client, 56, t["id"])).json()
    assert r["uploaded"]["ocr_status"] == "error"
    assert r["task"]["status"] == "manual_review"
    assert r["task"]["ocr_attempts_used"] == 0  # техническая ошибка не тратит попытку


async def test_error_report_and_manual_return(client):
    await owner_ready(client)
    emp = await make_active(client, 57)
    p = await ready_product(client, [emp], n_tasks=1, error_reasons=["Ссылка не открывается"])
    t = await take(client, 57, p["id"])
    h = auth(57)
    r = await client.post(
        f"/api/my/tasks/{t['id']}/report-error", json={"reason": "Неизвестно"}, headers=h
    )
    assert r.json()["code"] == "bad_reason"
    r = await client.post(
        f"/api/my/tasks/{t['id']}/report-error", json={"reason": "Ссылка не открывается"}, headers=h
    )
    assert r.json()["task"]["status"] == "manual_review"
    # Действия сотрудника заблокированы до решения.
    r = await client.post(f"/api/my/tasks/{t['id']}/open", headers=h)
    assert r.json()["code"] == "manual_review"
    r = await client.post(f"/api/manual-review/{t['id']}/return", json={}, headers=auth(OWNER_TG))
    assert r.json()["code"] == "reason_required"
    r = await client.post(
        f"/api/manual-review/{t['id']}/return",
        json={"reason": "Ссылка исправлена"},
        headers=auth(OWNER_TG),
    )
    assert r.json()["status"] == "active"


async def test_moderation_quality_and_earnings(client, session):
    await owner_ready(client)
    emp = await make_active(client, 58)
    p = await ready_product(client, [emp], n_tasks=5, active_task_limit=5)
    ids = []
    for _ in range(5):
        t = await take(client, 58, p["id"])
        await run_task(client, 58, t["id"])
        ids.append(t["id"])

    # Ожидающие модерации не считаются ни одобренными, ни отклонёнными.
    q = await quality(session, user_ids=[emp])
    assert (q.approved_units, q.rejected_units, q.pending_units, q.percent) == (0, 0, 5, None)

    for tid in ids[:4]:
        assert (await moderate(client, tid, "approve")).status_code == 200
    r = await moderate(client, ids[4], "reject")
    assert r.json()["code"] == "reason_required"
    assert (await moderate(client, ids[4], "reject", "Нет фото")).status_code == 200

    q = await quality(session, user_ids=[emp])
    assert (q.approved_units, q.rejected_units, q.percent) == (4, 1, 80.0)

    earnings = (
        (await session.execute(select(LedgerEntry).where(LedgerEntry.user_id == emp)))
        .scalars()
        .all()
    )
    assert len(earnings) == 4 and all(e.amount_kop == 5000 for e in earnings)

    # Изменение решения: одобренное → отклонено. Исходная запись остаётся, добавляется сторно.
    r = await moderate(client, ids[0], "reject", "Повторная проверка: подделка")
    assert r.status_code == 200
    entries = (
        (
            await session.execute(
                select(LedgerEntry).where(LedgerEntry.task_id == ids[0]).order_by(LedgerEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(e.kind, e.amount_kop) for e in entries] == [
        (EntryKind.EARNING, 5000),
        (EntryKind.EARNING_REVERSAL, -5000),
    ]
    q = await quality(session, user_ids=[emp])
    assert (q.approved_units, q.rejected_units, q.percent) == (3, 2, 60.0)

    # И обратно — новое начисление, без дублей.
    await moderate(client, ids[0], "approve")
    kinds = (
        (await session.execute(select(LedgerEntry.kind).where(LedgerEntry.task_id == ids[0])))
        .scalars()
        .all()
    )
    assert kinds.count(EntryKind.EARNING) == 2


async def test_moderation_permissions_and_own_tasks(client):
    await owner_ready(client)
    mod = await make_active(client, 59, role="admin")
    emp = await make_active(client, 60)
    p = await ready_product(client, [emp, mod], n_tasks=2)
    await set_role_rate(client, p["id"], "admin")
    t = await take(client, 60, p["id"])
    await run_task(client, 60, t["id"])
    assert (await moderate(client, t["id"], "approve", tg=59)).status_code == 403
    await grant(client, mod, "moderation.review")
    queue = (await client.get("/api/moderation", headers=auth(59))).json()
    assert [x["task"]["id"] for x in queue] == [t["id"]]
    # Модератор не проверяет собственное задание.
    own = await take(client, 59, p["id"])
    await run_task(client, 59, own["id"])
    assert (await moderate(client, own["id"], "approve", tg=59)).status_code == 403
    assert (await moderate(client, t["id"], "approve", tg=59)).status_code == 200


async def test_return_for_fix(client):
    await owner_ready(client)
    emp = await make_active(client, 61)
    p = await ready_product(client, [emp], n_tasks=1, final_photo_required=True)
    t = await take(client, 61, p["id"])
    await run_task(client, 61, t["id"], final_photo=True)
    r = await moderate(client, t["id"], "return", "Переснимите фото")
    assert r.json()["status"] == "active"
    assert (await photo(client, 61, t["id"])).status_code == 200
    v = (await client.post(f"/api/my/tasks/{t['id']}/complete", headers=auth(61))).json()
    assert v["task"]["status"] == "moderation"


async def test_approve_blocked_without_rate(client, session):
    await owner_ready(client)
    emp = await make_active(client, 62)
    p = await ready_product(client, [emp], n_tasks=1)
    t = await take(client, 62, p["id"])
    await run_task(client, 62, t["id"])
    await client.put(
        f"/api/products/{p['id']}/rates/roles/employee",
        json={"amount_kop": None},
        headers=auth(OWNER_TG),
    )
    r = await moderate(client, t["id"], "approve")
    assert r.status_code == 409 and r.json()["code"] == "rate_not_configured"
    task = await session.get(Task, t["id"])
    await session.refresh(task)
    assert task.status.value == "moderation"
