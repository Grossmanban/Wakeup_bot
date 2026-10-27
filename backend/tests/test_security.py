"""Этап 13: регрессионные тесты по итогам проверки безопасности (docs/security.md)."""

import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import select

from app import ocr, timeutil
from app.models import Task
from tests.helpers import (
    JPEG,
    OWNER_TG,
    auth,
    grant,
    make_active,
    moderate,
    owner_ready,
    photo,
    ready_product,
    run_task,
    take,
)

TODAY = timeutil.msk_today()


def key() -> str:
    return uuid.uuid4().hex


async def network(client, name="Север") -> int:
    r = await client.post("/api/networks", json={"name": name}, headers=auth(OWNER_TG))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def set_networks(client, uid, nets, tg=OWNER_TG):
    return await client.put(
        f"/api/users/{uid}/networks", json={"network_ids": nets}, headers=auth(tg)
    )


async def test_no_finance_actions_on_self(client):
    """Админ с финансовыми правами не может начислить бонус и выплату самому себе."""
    await owner_ready(client)
    net = await network(client)
    admin = await make_active(client, 300, role="admin")
    await grant(client, admin, "finance.manage", "payouts.manage", ("users.status", net))
    assert (await set_networks(client, admin, [net])).status_code == 200
    h = auth(300)
    r = await client.post(
        f"/api/users/{admin}/bonuses",
        json={"amount_kop": 10**10, "reason": "себе", "request_key": key()},
        headers=h,
    )
    assert r.status_code == 403 and r.json()["code"] == "self_action"
    r = await client.post(
        f"/api/users/{admin}/payouts",
        json={"start": str(TODAY), "end": str(TODAY), "idempotency_key": key()},
        headers=h,
    )
    assert r.status_code == 403
    r = await client.put(f"/api/users/{admin}/status", json={"status": "active"}, headers=h)
    assert r.status_code == 403
    # Над другими — работает.
    emp = await make_active(client, 301)
    r = await client.post(
        f"/api/users/{emp}/bonuses",
        json={"amount_kop": 100, "reason": "за работу", "request_key": key()},
        headers=h,
    )
    assert r.status_code == 201


async def test_network_admin_cannot_claim_people_without_network(client):
    await owner_ready(client)
    net = await network(client)
    admin = await make_active(client, 302, role="admin")
    victim = await make_active(client, 303)
    await grant(client, admin, ("org.manage", net), ("finance.manage", net))
    assert (await set_networks(client, victim, [net], tg=302)).status_code == 404
    assert (await set_networks(client, admin, [net], tg=302)).status_code == 403
    r = await client.post(
        f"/api/users/{victim}/penalties",
        json={"amount_kop": 100, "reason": "x", "request_key": key()},
        headers=auth(302),
    )
    assert r.status_code == 404
    # Человек из своей сети — можно.
    inside = await make_active(client, 304)
    assert (await set_networks(client, inside, [net])).status_code == 200
    assert (await set_networks(client, inside, [net], tg=302)).status_code == 200


async def test_company_wide_rules_need_global_grant(client):
    await owner_ready(client)
    coach = await make_active(client, 305, role="coach")
    await grant(client, coach, "finance.manage")
    p = await ready_product(client, [], n_tasks=0)
    rule = {
        "name": "Бонус",
        "amount_kop": 10**9,
        "conditions": [{"product_id": p["id"], "min_units": 1}],
        "period_from": str(TODAY - timedelta(days=1)),
        "period_to": str(TODAY + timedelta(days=1)),
    }
    r = await client.post("/api/bonus-rules", json=rule, headers=auth(305))
    assert r.status_code == 403 and r.json()["code"] == "company_wide_only"
    lazy = {
        "product_id": p["id"],
        "min_daily_units": 5,
        "reduced_rate_kop": 0,
        "period_from": str(TODAY),
    }
    assert (await client.post("/api/lazy-rules", json=lazy, headers=auth(305))).status_code == 403
    admin = await make_active(client, 306, role="admin")
    await grant(client, admin, "finance.manage")
    assert (await client.post("/api/bonus-rules", json=rule, headers=auth(306))).status_code == 201


async def test_cannot_edit_someone_elses_broadcast_draft(client):
    await owner_ready(client)
    a = await make_active(client, 307, role="admin")
    await grant(client, a, "broadcasts.send")
    r = await client.post(
        "/api/broadcasts",
        json={"text": "Текст владельца", "audience": {"user_ids": [a]}},
        headers=auth(OWNER_TG),
    )
    bid = r.json()["id"]
    r = await client.put(
        f"/api/broadcasts/{bid}",
        json={"text": "<a href='https://evil.example'>вход</a>", "audience": {"all": True}},
        headers=auth(307),
    )
    assert r.status_code == 404
    r = await client.post(
        f"/api/broadcasts/{bid}/photo",
        files={"file": ("p.jpg", JPEG, "image/jpeg")},
        headers=auth(307),
    )
    assert r.status_code == 404


async def test_previous_assignee_photos_do_not_count(client):
    await owner_ready(client)
    a = await make_active(client, 308)
    b = await make_active(client, 309)
    p = await ready_product(
        client,
        [a, b],
        n_tasks=1,
        flow=[{"title": "Этап", "photo": "required"}],
        final_photo_required=True,
    )
    t = await take(client, 308, p["id"])
    ha = auth(308)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=ha)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=ha)
    assert (await photo(client, 308, t["id"], "stage", 0)).status_code == 200
    r = await client.post(
        f"/api/my/tasks/{t['id']}/stages/0/complete", json={"checklist": []}, headers=ha
    )
    assert r.status_code == 200, r.text
    assert (await photo(client, 308, t["id"], "final")).status_code == 200
    r = await client.post(
        f"/api/tasks/{t['id']}/return-to-pool", json={"reason": "r"}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 200, r.text

    assert (await take(client, 309, p["id"]))["id"] == t["id"]
    hb = auth(309)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=hb)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=hb)
    assert (await client.get(f"/api/my/tasks/{t['id']}", headers=hb)).json()["photos"] == []
    assert (await client.get(f"/api/tasks/{t['id']}/photos", headers=hb)).json() == []
    r = await client.post(
        f"/api/my/tasks/{t['id']}/stages/0/complete", json={"checklist": []}, headers=hb
    )
    assert r.status_code == 409
    # Руководитель видит всю историю.
    hist = (await client.get(f"/api/tasks/{t['id']}/photos", headers=auth(OWNER_TG))).json()
    assert [x["attempt"] for x in hist] == [1, 1]


async def test_return_for_fix_requires_new_photo(client):
    await owner_ready(client)
    a = await make_active(client, 310)
    p = await ready_product(client, [a], n_tasks=1, final_photo_required=True)
    t = await take(client, 310, p["id"])
    await run_task(client, 310, t["id"], final_photo=True)
    assert (await moderate(client, t["id"], "return", "Переснять фото")).status_code == 200
    r = await client.post(f"/api/my/tasks/{t['id']}/complete", headers=auth(310))
    assert r.status_code == 409
    assert (await photo(client, 310, t["id"])).status_code == 200
    r = await client.post(f"/api/my/tasks/{t['id']}/complete", headers=auth(310))
    assert r.status_code == 200 and r.json()["task"]["status"] == "moderation"


class SlowOcr:
    async def recognize(self, path):
        await asyncio.sleep(0.2)
        return "не то"


async def test_parallel_uploads_do_not_exceed_ocr_attempts(client):
    await owner_ready(client)
    a = await make_active(client, 311)
    p = await ready_product(
        client,
        [a],
        n_tasks=1,
        ocr_enabled=True,
        ocr_attempts=2,
        expected_text_default="Спасибо за отзыв",
    )
    t = await take(client, 311, p["id"])
    h = auth(311)
    await client.post(f"/api/my/tasks/{t['id']}/open", headers=h)
    await client.post(f"/api/my/tasks/{t['id']}/found", headers=h)
    ocr.set_engine(SlowOcr())
    results = await asyncio.gather(*(photo(client, 311, t["id"]) for _ in range(5)))
    assert sorted(r.status_code for r in results) == [200, 200, 409, 409, 409]
    task = (await client.get(f"/api/my/tasks/{t['id']}", headers=h)).json()["task"]
    assert task["ocr_attempts_used"] == 2 and task["status"] == "manual_review"


async def test_goals_of_other_teams_are_hidden(client):
    await owner_ready(client)
    coach = await make_active(client, 312, role="coach")
    other = await make_active(client, 313)
    await grant(client, coach, "goals.manage")
    p = await ready_product(client, [], n_tasks=0)
    r = await client.post(
        "/api/goals",
        json={
            "title": "Цель владельца",
            "kind": "goal",
            "participants_spec": {"type": "users", "ids": [other]},
            "product_ids": [p["id"]],
            "target_units": 10,
            "period_from": str(TODAY),
            "period_to": str(TODAY + timedelta(days=5)),
        },
        headers=auth(OWNER_TG),
    )
    gid = r.json()["id"]
    assert (await client.get(f"/api/goals/{gid}", headers=auth(312))).status_code == 404
    r = await client.put(f"/api/goals/{gid}/status", json={"status": "deleted"}, headers=auth(312))
    assert r.status_code == 404
    listed = (await client.get("/api/goals", headers=auth(312))).json()
    assert all(g["id"] != gid for g in listed)


async def test_invites_to_owner_groups_and_foreign_invites(client, gateway):
    await owner_ready(client)
    net = await network(client)
    a = await make_active(client, 314, role="admin")
    emp = await make_active(client, 315)
    await grant(client, a, ("groups.invite", net))
    await set_networks(client, emp, [net])
    r = await client.post("/api/groups", json={"chat_id": -100777}, headers=auth(OWNER_TG))
    gid = r.json()["id"]
    await client.patch(f"/api/groups/{gid}", json={"purpose": "audit"}, headers=auth(OWNER_TG))
    r = await client.post(f"/api/groups/{gid}/invite", json={"user_id": emp}, headers=auth(314))
    assert r.status_code == 403
    # Приглашение, созданное владельцем человеку вне сети админа, админ не видит.
    outsider = await make_active(client, 316)
    r = await client.post(
        f"/api/groups/{gid}/invite", json={"user_id": outsider}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/groups/invites/{r.json()['id']}", headers=auth(314))
    assert r.status_code == 404


async def test_take_replay_does_not_return_foreign_task(client):
    await owner_ready(client)
    a = await make_active(client, 317)
    b = await make_active(client, 318)
    p = await ready_product(client, [a, b], n_tasks=1)
    h = {**auth(317), "Idempotency-Key": "take-1"}
    t = (await client.post(f"/api/my/products/{p['id']}/take", headers=h)).json()
    await client.post(
        f"/api/tasks/{t['id']}/return-to-pool", json={"reason": "r"}, headers=auth(OWNER_TG)
    )
    assert (await take(client, 318, p["id"]))["id"] == t["id"]
    r = await client.post(f"/api/my/products/{p['id']}/take", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "task_gone"


async def test_network_task_manager_cannot_touch_other_pools(client, session):
    await owner_ready(client)
    net = await network(client)
    m = await make_active(client, 319, role="admin")
    await grant(client, m, ("tasks.manage", net))
    p = await ready_product(client, [], n_tasks=1)
    tid = await session.scalar(select(Task.id).where(Task.product_id == p["id"]))
    r = await client.post(f"/api/tasks/{tid}/cancel", json={"reason": "x"}, headers=auth(319))
    assert r.status_code == 404


async def test_start_training_closes_pending_registration(client, session):
    """«Начать обучение» закрывает открытую заявку: её отклонение не отключит стажёра."""
    from tests.test_people import _fill

    await owner_ready(client)
    h = auth(OWNER_TG)
    await _fill(client, 320)
    cid = (await client.post("/api/my/candidate/submit", headers=auth(320))).json()["candidate"][
        "id"
    ]
    reg = await client.post(
        "/api/registration",
        json={"first_name": "Ольга", "last_name": "Заявкина"},
        headers=auth(320),
    )
    await client.post(f"/api/candidates/{cid}/review/take", json={}, headers=h)
    await client.post(f"/api/candidates/{cid}/review/approve", json={}, headers=h)
    r = await client.post(f"/api/candidates/{cid}/start-training", headers=h)
    assert r.json()["status"] == "active", r.text
    assert reg.status_code == 200, reg.text
    r = await client.post(
        f"/api/registrations/{reg.json()['id']}/reject", json={"reason": "x"}, headers=h
    )
    assert r.status_code in (404, 409), r.text
    assert (await client.get("/api/me", headers=auth(320))).json()["screen"] == "ready"
