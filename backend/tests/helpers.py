"""Общие функции тестов. Не импортирует app, чтобы conftest успел выставить окружение."""

import json
import time

BOT_TOKEN = "123456:TEST-TOKEN"
OWNER_TG = 1000


def init_data(
    tg_id: int, *, first_name: str = "Тест", username: str | None = None, auth_date=None
) -> str:
    user = {"id": tg_id, "first_name": first_name}
    if username:
        user["username"] = username
    from app.security.telegram_auth import sign_init_data

    return sign_init_data(
        {
            "auth_date": str(int(auth_date if auth_date is not None else time.time())),
            "query_id": "AAH",
            "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
        },
        BOT_TOKEN,
    )


def auth(tg_id: int, **kw) -> dict[str, str]:
    return {"Authorization": f"tma {init_data(tg_id, **kw)}"}


async def register(client, tg_id: int, first: str = "Иван", last: str = "Иванов", **kw):
    r = await client.post(
        "/api/registration",
        json={"first_name": first, "last_name": last},
        headers=auth(tg_id, **kw),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def owner_ready(client):
    """Владелец входит и сохраняет своё имя."""
    await register(client, OWNER_TG, "Олег", "Владельцев")


async def make_active(
    client, tg_id: int, first: str = "Иван", last: str = "Иванов", role=None
) -> int:
    """Зарегистрировать человека, одобрить заявку от владельца, при необходимости назначить роль."""
    req = await register(client, tg_id, first, last)
    r = await client.post(f"/api/registrations/{req['id']}/approve", headers=auth(OWNER_TG))
    assert r.status_code == 200, r.text
    user_id = req["user_id"]
    if role:
        r = await client.put(
            f"/api/users/{user_id}/role", json={"role": role}, headers=auth(OWNER_TG)
        )
        assert r.status_code == 200, r.text
    return user_id


async def grant(client, user_id: int, *grants):
    body = {
        "grants": [
            {"permission": g[0], "network_id": g[1]} if isinstance(g, tuple) else {"permission": g}
            for g in grants
        ]
    }
    r = await client.put(f"/api/users/{user_id}/permissions", json=body, headers=auth(OWNER_TG))
    assert r.status_code == 200, r.text
