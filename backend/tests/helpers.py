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
    """Владелец указывает ФИО через API. Для остальных — заявка в ожидании на уровне сервиса,
    как у людей, подавших её до скрытого режима: самостоятельная регистрация через API
    закрыта (test_hidden_mode.py), новые сотрудники приходят по приглашению."""
    if tg_id == OWNER_TG:
        r = await client.post(
            "/api/registration",
            json={"first_name": first, "last_name": last},
            headers=auth(tg_id, **kw),
        )
        assert r.status_code == 200, r.text
        return r.json()
    from app.db import get_sessionmaker
    from app.security.telegram_auth import TelegramUser
    from app.services import users as users_svc

    async with get_sessionmaker()() as s:
        user = await users_svc.upsert_from_telegram(
            s, TelegramUser(tg_id, kw.get("first_name", "Тест"), None, kw.get("username"))
        )
        await s.commit()
        req = await users_svc.submit_registration(s, user, first, last)
        return {
            "id": req.id,
            "user_id": user.id,
            "status": req.status.value,
            "first_name": req.first_name,
        }


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


def xlsx(rows: list[list], header: list[str]) -> bytes:
    """Собрать .xlsx в памяти."""
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def create_product(client, name: str = "Продукт А", **settings) -> dict:
    r = await client.post("/api/products", json={"name": name, **settings}, headers=auth(OWNER_TG))
    assert r.status_code == 201, r.text
    return r.json()


async def set_role_rate(client, product_id: int, role: str = "employee", kop: int | None = 5000):
    r = await client.put(
        f"/api/products/{product_id}/rates/roles/{role}",
        json={"amount_kop": kop},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200, r.text


async def give_products(client, user_id: int, *product_ids: int):
    r = await client.put(
        f"/api/users/{user_id}/products", json={"ids": list(product_ids)}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 200, r.text


async def import_tasks(client, product_id: int, ids: list[str], **cols) -> dict:
    header = ["ID задания", "Ссылка", *cols.keys()]
    rows = [
        [i, f"https://example.com/{i}", *[v[n] for v in cols.values()]] for n, i in enumerate(ids)
    ]
    r = await client.post(
        "/api/imports",
        files={"file": ("t.xlsx", xlsx(rows, header))},
        data={"product_id": str(product_id)},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200, r.text
    batch = r.json()
    r = await client.post(f"/api/imports/{batch['id']}/commit", headers=auth(OWNER_TG))
    assert r.status_code == 200, r.text
    return r.json()


async def ready_product(client, user_ids: list[int], n_tasks: int = 3, **settings) -> dict:
    """Продукт со ставкой, доступом для людей и заданиями в пуле."""
    p = await create_product(client, **settings)
    await set_role_rate(client, p["id"])
    for uid in user_ids:
        await give_products(client, uid, p["id"])
    if n_tasks:
        await import_tasks(client, p["id"], [f"T{i}" for i in range(1, n_tasks + 1)])
    return p


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


async def take(client, tg_id: int, product_id: int) -> dict:
    r = await client.post(f"/api/my/products/{product_id}/take", headers=auth(tg_id))
    assert r.status_code == 200, r.text
    return r.json()


async def photo(client, tg_id: int, task_id: int, kind: str = "final", stage: int | None = None):
    data = {"kind": kind}
    if stage is not None:
        data["stage"] = str(stage)
    return await client.post(
        f"/api/my/tasks/{task_id}/photos",
        files={"file": ("p.jpg", JPEG, "image/jpeg")},
        data=data,
        headers=auth(tg_id),
    )


async def run_task(client, tg_id: int, task_id: int, *, final_photo: bool = False) -> dict:
    """Открыть ссылку, «Найдено», (фото) и завершить задание без этапов."""
    h = auth(tg_id)
    assert (await client.post(f"/api/my/tasks/{task_id}/open", headers=h)).status_code == 200
    assert (await client.post(f"/api/my/tasks/{task_id}/found", headers=h)).status_code == 200
    if final_photo:
        assert (await photo(client, tg_id, task_id)).status_code == 200
    r = await client.post(f"/api/my/tasks/{task_id}/complete", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def moderate(client, task_id: int, decision: str, reason: str | None = None, tg=OWNER_TG):
    r = await client.post(
        f"/api/moderation/{task_id}",
        json={"decision": decision, "reason": reason},
        headers=auth(tg),
    )
    return r
