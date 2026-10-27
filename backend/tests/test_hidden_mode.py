"""Скрытый режим бота, реферальная регистрация и приглашение действующих сотрудников.

Номера в комментариях — критерии приёмки из раздела 8 ТЗ доработки (docs/hidden-mode.md).
"""

from datetime import timedelta

from sqlalchemy import func, select, update

from app.db import get_sessionmaker
from app.models import AccessAttempt, AccessEvent, Candidate, StaffInvite, User
from app.security.telegram_auth import TelegramUser
from app.services import entry
from tests.helpers import OWNER_TG, auth, grant, make_active, owner_ready


def tg(tg_id: int, first="Гость", username=None) -> TelegramUser:
    return TelegramUser(tg_id, first, None, username)


async def start(tg_id: int, payload: str = "", **kw):
    async with get_sessionmaker()() as s:
        return await entry.handle_start(s, tg(tg_id, **kw), payload)


async def new_invite(client, headers=None, **body) -> dict:
    r = await client.post("/api/staff-invites", json=body, headers=headers or auth(OWNER_TG))
    assert r.status_code == 201, r.text
    return r.json()


def token(inv: dict) -> str:
    return inv["link"].split("start=", 1)[1]


async def test_stranger_is_ignored_and_logged(client, session):
    await owner_ready(client)
    # 1. Без приглашения бот молчит — и на /start, и на любые параметры.
    assert await start(500, username="stranger") is None
    assert await start(500, "ref_nosuchcode") is None
    assert await start(500, "inv_" + "x" * 32) is None
    # Аккаунт не создаётся.
    assert await session.scalar(select(User).where(User.telegram_id == 500)) is None
    # 2. Telegram ID записан, повторы не плодят записи человека.
    a = await session.get(AccessAttempt, 500)
    assert a.attempts == 3 and a.username == "stranger"
    assert a.last_source == "inv_…"  # токен в журнал не попадает
    # Сообщения и кнопки тоже записываются и не обрабатываются.
    async with get_sessionmaker()() as s:
        assert await entry.note_interaction(s, tg(500), "message", "привет") is False

    # 3. Администратор с правом видит журнал и карточку с историей.
    admin = await make_active(client, 501, role="admin")
    assert (await client.get("/api/access-attempts", headers=auth(501))).status_code == 403
    await grant(client, admin, "access_log.view")
    rows = (await client.get("/api/access-attempts", headers=auth(501))).json()
    row = next(r for r in rows if r["telegram_id"] == 500)
    assert row["status_title"] == "Нет приглашения" and row["attempts"] == 4
    card = (await client.get("/api/access-attempts/500", headers=auth(501))).json()
    assert [e["kind"] for e in card["events"]] == ["start", "start", "start", "message"]
    assert all(e["result"] == "ignored" for e in card["events"])
    assert card["events"][3]["text"] == "привет"

    # Открыть Mini App напрямую тоже нельзя.
    me = (await client.get("/api/me", headers=auth(502))).json()
    assert me["screen"] == "no_access"
    assert await session.scalar(
        select(func.count()).where(AccessEvent.telegram_id == 502, AccessEvent.kind == "app")
    )


async def test_referral_candidate_flow(client, session):
    await owner_ready(client)
    ref_user = await make_active(client, 510)
    code_plain = (await client.get("/api/my/referral", headers=auth(OWNER_TG))).json()["code"]

    # Ссылка сотрудника без права на рефералов недействительна.
    async with get_sessionmaker()() as s:
        u = await s.get(User, ref_user)
        u.referral_code = "nopermcode"
        await s.commit()
    assert await start(511, "ref_nopermcode") is None

    # 4. Действительная ссылка: приветствие и анкета кандидата.
    reply = await start(511, f"ref_{code_plain}")
    assert reply is not None and "анкету" in reply.text and reply.show_app
    me = (await client.get("/api/me", headers=auth(511))).json()
    assert me["screen"] == "candidate_form"
    r = await client.put("/api/my/candidate", json={"first_name": "Ольга"}, headers=auth(511))
    assert r.status_code == 200
    # Повторный /start без параметра — снова приветствие, а не молчание.
    assert (await start(511)).show_app

    # Без ссылки анкету не начать даже прямым запросом к API.
    r = await client.put("/api/my/candidate", json={"first_name": "Олег"}, headers=auth(512))
    assert r.status_code == 403 and r.json()["code"] == "referral_required"
    # 12. Реферальная ссылка не даёт упрощённую регистрацию действующего сотрудника.
    r = await client.post(
        "/api/staff-registration",
        json={"first_name": "Ольга", "last_name": "Новикова"},
        headers=auth(511),
    )
    assert r.status_code == 403 and r.json()["code"] == "invite_required"


async def test_self_registration_is_closed(client):
    """5. Самостоятельная заявка «Я уже работаю» убрана и на сервере."""
    await owner_ready(client)
    r = await client.post(
        "/api/registration", json={"first_name": "Иван", "last_name": "Иванов"}, headers=auth(520)
    )
    assert r.status_code == 403 and r.json()["code"] == "invite_required"


async def test_staff_invite_flow(client, session):
    await owner_ready(client)
    # 6, 7. Владелец создаёт приглашение и получает ссылку.
    inv = await new_invite(client, note="Для Ивана", expires_in_hours=48)
    assert inv["link"].startswith("https://t.me/wakeup_test_bot?start=inv_")
    assert inv["status"] == "active" and inv["max_uses"] == 1 and inv["kind_title"]
    t = token(inv)
    assert len(t) >= 36  # inv_ + 32 символа токена

    reply = await start(530, t, first="Иван")
    assert reply is not None and "Приглашение подтверждено" in reply.text
    me = (await client.get("/api/me", headers=auth(530))).json()
    assert me["screen"] == "staff_register"

    # 8. Только данные — и сразу рабочий интерфейс (10), без анкеты и стажировки (9).
    r = await client.post(
        "/api/staff-registration",
        json={"first_name": "Иван", "last_name": "Действующий"},
        headers=auth(530),
    )
    assert r.status_code == 200, r.text
    me = (await client.get("/api/me", headers=auth(530))).json()
    assert me["screen"] == "ready"
    assert me["user"]["role"] == "employee" and me["user"]["full_name"] == "Действующий Иван"
    assert me["training_status"] == "none"
    u = await session.scalar(select(User).where(User.telegram_id == 530))
    assert await session.scalar(select(Candidate).where(Candidate.user_id == u.id)) is None

    # Админка: использовано, видно кем.
    lst = (await client.get("/api/staff-invites", headers=auth(OWNER_TG))).json()
    assert lst[0]["status"] == "used" and lst[0]["link"] is None
    assert lst[0]["claims"][0]["telegram_id"] == 530 and lst[0]["claims"][0]["used_at"]

    # 11. Второй раз ссылка не работает.
    assert await start(531, t) is None
    # В журнале у сотрудника — «Зарегистрирован».
    a = (await client.get("/api/access-attempts/530", headers=auth(OWNER_TG))).json()
    assert a["status"] == "registered"
    assert [e["kind"] for e in a["events"]] == ["invite_claimed", "invite_used"]


async def test_invite_protection(client, session):
    await owner_ready(client)
    # Создавать приглашения — только с явным разрешением.
    admin = await make_active(client, 540, role="admin")
    r = await client.post("/api/staff-invites", json={}, headers=auth(540))
    assert r.status_code == 403
    await grant(client, admin, "invites.staff")
    inv = await new_invite(client, headers=auth(540))
    t = token(inv)

    # 11. Подмена параметров: другой токен, тип ссылки, усечённый токен — молчание.
    assert await start(541, "inv_" + t[4:-1] + ("A" if t[-1] != "A" else "B")) is None
    assert await start(541, "ref_" + t[4:]) is None
    assert await start(541, t[:-3]) is None

    # Двое открыли одноразовую ссылку — зарегистрироваться успевает только первый.
    assert await start(542, t) and await start(543, t)
    body = {"first_name": "Анна", "last_name": "Первая"}
    assert (
        await client.post("/api/staff-registration", json=body, headers=auth(542))
    ).status_code == 200
    r = await client.post(
        "/api/staff-registration", json={**body, "last_name": "Вторая"}, headers=auth(543)
    )
    assert r.status_code == 403
    # Без перехода по ссылке — нельзя.
    r = await client.post("/api/staff-registration", json=body, headers=auth(544))
    assert r.status_code == 403

    # Отзыв и истечение.
    inv2 = await new_invite(client, headers=auth(540), max_uses=3)
    r = await client.post(f"/api/staff-invites/{inv2['id']}/revoke", headers=auth(540))
    assert r.json()["status"] == "revoked"
    assert await start(545, token(inv2)) is None
    inv3 = await new_invite(client)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(StaffInvite)
            .where(StaffInvite.id == inv3["id"])
            .values(expires_at=func.now() - timedelta(minutes=1))
        )
        await s.commit()
    assert await start(546, token(inv3)) is None
    # Отозвать чужое приглашение админ не может.
    r = await client.post(f"/api/staff-invites/{inv3['id']}/revoke", headers=auth(540))
    assert r.status_code == 404
    # Приглашение не даёт прав выше сотрудника.
    me = (await client.get("/api/me", headers=auth(542))).json()
    assert me["user"]["role"] == "employee" and me["permissions"] == []


async def test_existing_employee_keeps_account(client, session):
    """13. Существующий сотрудник: без дубликата, роли не меняются, приглашение не тратится."""
    await owner_ready(client)
    uid = await make_active(client, 550, role="coach")
    inv = await new_invite(client)
    reply = await start(550, token(inv))
    assert reply is not None and "уже зарегистрированы" in reply.text
    assert await session.scalar(select(func.count()).where(User.telegram_id == 550)) == 1
    u = await session.get(User, uid)
    assert u.role == "coach"
    lst = (await client.get("/api/staff-invites", headers=auth(OWNER_TG))).json()
    assert lst[0]["uses"] == 0 and lst[0]["status"] == "active"
    # Обычный /start по-прежнему открывает приложение.
    assert (await start(550)).show_app
    assert (await client.get("/api/me", headers=auth(550))).json()["screen"] == "ready"
