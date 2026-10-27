import asyncio

from sqlalchemy import func, select, text

from app.models import AuditEvent, TelegramOutbox, User
from tests.helpers import OWNER_TG, auth, make_active, owner_ready, register


async def test_owner_bootstrap(client):
    r = await client.get("/api/me", headers=auth(OWNER_TG))
    me = r.json()
    assert me["user"]["role"] == "owner"
    assert me["user"]["status"] == "active"
    assert me["screen"] == "register"  # владелец ещё не указал ФИО
    await owner_ready(client)
    me = (await client.get("/api/me", headers=auth(OWNER_TG))).json()
    assert me["screen"] == "ready"
    assert me["user"]["full_name"] == "Владельцев Олег"


async def test_full_registration_flow(client, session):
    await owner_ready(client)

    me = (await client.get("/api/me", headers=auth(10, username="petr"))).json()
    assert me["screen"] == "register"
    assert me["has_product_access"] is False
    assert me["product_access_message"].startswith("Вам пока не выданы доступы")

    req = await register(client, 10, "Пётр", "Петров", username="petr")
    assert req["status"] == "pending"
    me = (await client.get("/api/me", headers=auth(10, username="petr"))).json()
    assert me["screen"] == "pending"

    # Пока заявка не одобрена, рабочие разделы закрыты.
    r = await client.get("/api/users", headers=auth(10, username="petr"))
    assert r.status_code == 403
    assert r.json()["code"] == "not_registered"

    # Владельцу ушло уведомление о новой заявке.
    owner_msgs = (
        (await session.execute(select(TelegramOutbox).where(TelegramOutbox.chat_id == OWNER_TG)))
        .scalars()
        .all()
    )
    assert len(owner_msgs) == 1
    assert "Петров Пётр" in owner_msgs[0].payload["text"]

    lst = (await client.get("/api/registrations", headers=auth(OWNER_TG))).json()
    assert [x["id"] for x in lst] == [req["id"]]
    assert lst[0]["user"]["username"] == "petr"

    r = await client.post(f"/api/registrations/{req['id']}/approve", headers=auth(OWNER_TG))
    assert r.status_code == 200
    me = (await client.get("/api/me", headers=auth(10))).json()
    assert me["screen"] == "ready"
    assert me["user"]["role"] == "employee"
    assert me["user"]["full_name"] == "Петров Пётр"

    # Повторное одобрение — конфликт, а не второе уведомление.
    r = await client.post(f"/api/registrations/{req['id']}/approve", headers=auth(OWNER_TG))
    assert r.status_code == 409
    decisions = await session.scalar(
        select(func.count()).where(TelegramOutbox.dedup_key == f"reg:{req['id']}:decision")
    )
    assert decisions == 1

    actions = (
        (await session.execute(select(AuditEvent.action).order_by(AuditEvent.id))).scalars().all()
    )
    assert "registration.submitted" in actions
    assert "registration.approved" in actions


async def test_concurrent_approve_only_one_wins(client):
    await owner_ready(client)
    req = await register(client, 11)
    url = f"/api/registrations/{req['id']}/approve"
    results = await asyncio.gather(*(client.post(url, headers=auth(OWNER_TG)) for _ in range(5)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409, 409]


async def test_one_profile_per_telegram_id(client, session):
    await asyncio.gather(*(client.get("/api/me", headers=auth(12)) for _ in range(5)))
    n = await session.scalar(select(func.count()).where(User.telegram_id == 12))
    assert n == 1


async def test_reject_with_reason_and_reapply(client):
    await owner_ready(client)
    req = await register(client, 13)
    r = await client.post(
        f"/api/registrations/{req['id']}/reject",
        json={"reason": "Нет в списке стажёров"},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200
    me = (await client.get("/api/me", headers=auth(13))).json()
    assert me["screen"] == "rejected"
    assert me["registration"]["reason"] == "Нет в списке стажёров"

    again = await register(client, 13, "Иван", "Сидоров")
    assert again["id"] != req["id"]
    assert (await client.get("/api/me", headers=auth(13))).json()["screen"] == "pending"


async def test_pending_application_can_be_corrected(client):
    req1 = await register(client, 14, "Ивн", "Иванов")
    req2 = await register(client, 14, "Иван", "Иванов")
    assert req1["id"] == req2["id"]
    assert req2["first_name"] == "Иван"


async def test_invalid_names(client):
    r = await client.post(
        "/api/registration", json={"first_name": "  ", "last_name": "Иванов"}, headers=auth(15)
    )
    assert r.status_code == 400
    r = await client.post(
        "/api/registration", json={"first_name": "<b>", "last_name": "Иванов"}, headers=auth(15)
    )
    assert r.status_code == 400


async def test_name_change_flow(client, session):
    await owner_ready(client)
    uid = await make_active(client, 20, "Анна", "Смирнова")

    r = await client.post(
        "/api/me/name-change",
        json={"first_name": "Анна", "last_name": "Кузнецова"},
        headers=auth(20),
    )
    assert r.status_code == 200
    nc = r.json()
    # Имя не меняется до подтверждения.
    me = (await client.get("/api/me", headers=auth(20))).json()
    assert me["user"]["last_name"] == "Смирнова"
    assert me["name_change"]["status"] == "pending"

    lst = (await client.get("/api/name-changes", headers=auth(OWNER_TG))).json()
    assert [x["id"] for x in lst] == [nc["id"]]

    r = await client.post(f"/api/name-changes/{nc['id']}/approve", headers=auth(OWNER_TG))
    assert r.status_code == 200
    me = (await client.get("/api/me", headers=auth(20))).json()
    assert me["user"]["last_name"] == "Кузнецова"

    ev = await session.scalar(select(AuditEvent).where(AuditEvent.action == "name_change.approved"))
    assert ev.old_value == {"first_name": "Анна", "last_name": "Смирнова"}
    assert ev.new_value == {"first_name": "Анна", "last_name": "Кузнецова"}
    assert ev.target_user_id == uid


async def test_audit_is_append_only(client, session):
    await owner_ready(client)
    ev_id = await session.scalar(select(AuditEvent.id).limit(1))
    assert ev_id is not None
    for stmt in (
        "UPDATE audit_events SET reason = 'x' WHERE id = :id",
        "DELETE FROM audit_events WHERE id = :id",
    ):
        try:
            await session.execute(text(stmt), {"id": ev_id})
        except Exception as e:  # noqa: BLE001
            assert "append-only" in str(e)
            await session.rollback()
        else:
            raise AssertionError("audit_events must be append-only")
