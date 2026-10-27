"""Этап 3: продукты, ставки, доступы, импорт из Excel, общий пул."""

import asyncio

from sqlalchemy import func, select

from app.models import Task, TaskStatus
from tests.helpers import (
    OWNER_TG,
    auth,
    create_product,
    give_products,
    grant,
    import_tasks,
    make_active,
    owner_ready,
    ready_product,
    set_role_rate,
    xlsx,
)


async def test_only_owner_manages_products(client):
    await owner_ready(client)
    admin = await make_active(client, 30, role="admin")
    await grant(client, admin, "tasks.manage", "tasks.import")
    r = await client.post("/api/products", json={"name": "X"}, headers=auth(30))
    assert r.status_code == 403
    p = await create_product(
        client,
        "Яндекс",
        active_task_limit=2,
        flow=[{"title": "Найти", "checklist": ["Открыл карточку"], "min_seconds": 30}],
        links=[{"title": "Инструкция", "url": "https://example.com/doc"}],
    )
    assert p["flow"][0]["min_seconds"] == 30
    assert p["links"][0]["url"].startswith("https://example.com")
    # Админ с правами на задания видит продукты, но не меняет их.
    assert (await client.get("/api/products", headers=auth(30))).status_code == 200
    r = await client.patch(f"/api/products/{p['id']}", json={"name": "Y"}, headers=auth(30))
    assert r.status_code == 403
    # Дубликат названия.
    r = await client.post("/api/products", json={"name": "яндекс"}, headers=auth(OWNER_TG))
    assert r.status_code == 409


async def test_rate_priority_and_missing_rate(client):
    await owner_ready(client)
    emp = await make_active(client, 31)
    p = await create_product(client)
    await give_products(client, emp, p["id"])
    await import_tasks(client, p["id"], ["A1"])

    # Ставка не настроена — задание не выдаётся, ноль не подставляется.
    r = await client.post(f"/api/my/products/{p['id']}/take", headers=auth(31))
    assert r.status_code == 409
    assert r.json()["code"] == "rate_not_configured"
    mine = (await client.get("/api/my/products", headers=auth(31))).json()
    assert mine[0]["rate_kop"] is None and "ставка" in mine[0]["rate_error"]

    await set_role_rate(client, p["id"], "employee", 5000)
    r = await client.put(
        f"/api/products/{p['id']}/rates/users/{emp}",
        json={"amount_kop": 7000},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200
    mine = (await client.get("/api/my/products", headers=auth(31))).json()
    assert mine[0]["rate_kop"] == 7000  # индивидуальная важнее ставки роли

    # Снятие индивидуальной ставки возвращает ставку роли; история сохраняется.
    r = await client.put(
        f"/api/products/{p['id']}/rates/users/{emp}",
        json={"amount_kop": None},
        headers=auth(OWNER_TG),
    )
    rates = r.json()
    assert rates["roles"] == {"employee": 5000}
    assert rates["users"] == []
    assert len(rates["history"]) == 3
    mine = (await client.get("/api/my/products", headers=auth(31))).json()
    assert mine[0]["rate_kop"] == 5000


async def test_product_access_message(client):
    await owner_ready(client)
    emp = await make_active(client, 32)
    me = (await client.get("/api/me", headers=auth(32))).json()
    assert me["has_product_access"] is False
    p = await create_product(client)
    await give_products(client, emp, p["id"])
    me = (await client.get("/api/me", headers=auth(32))).json()
    assert me["has_product_access"] is True
    assert me["product_access_message"] is None
    # Выдавать доступы может только владелец.
    admin = await make_active(client, 33, role="admin")
    await grant(client, admin, "users.view", "tasks.manage")
    r = await client.put(f"/api/users/{emp}/products", json={"ids": []}, headers=auth(33))
    assert r.status_code == 403


async def test_import_preview_errors_duplicates_and_commit(client):
    await owner_ready(client)
    p = await create_product(client, "Альфа", required_fields=["link"])
    r = await client.put(
        f"/api/products/{p['id']}/task-types",
        json=[
            {"name": "Отзыв", "excel_names": ["review", "отзыв 5*"], "units": 2},
            {"name": "Фото", "excel_names": ["photo"]},
        ],
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200
    header = ["ID задания", "Тип", "Ссылка", "Пол", "Комментарий клиента"]
    rows = [
        ["1", "review", "https://a.ru/1", "ж", "срочно"],
        ["2", "Фото", "https://a.ru/2", "", None],
        ["3", "неизвестно", "https://a.ru/3", "", None],
        ["4", "photo", "", "", None],  # нет обязательной ссылки
        ["1", "photo", "https://a.ru/1b", "", None],  # повтор ID в файле
        [None, None, None, None, None],  # пустая строка
        ["5", "photo", "ftp://bad", "м", None],
        ["6", "photo", "https://a.ru/6", "оно", None],
    ]
    content = xlsx(rows, header)
    r = await client.post(
        "/api/imports",
        files={"file": ("tasks.xlsx", content)},
        data={"product_id": str(p["id"])},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200, r.text
    prev = r.json()
    s = prev["summary"]
    assert (s["ok"], s["errors"], s["duplicates"], s["skipped"]) == (2, 4, 1, 1)
    assert s["extra_columns"] == ["Комментарий клиента"]
    by_row = {x["row"]: x for x in prev["rows"]}
    assert by_row[2]["units"] == 2 and by_row[2]["gender"] == "f"
    assert by_row[2]["extra"] == {"Комментарий клиента": "срочно"}
    assert "Неизвестный тип" in by_row[4]["errors"][0]
    assert "Ссылка" in by_row[5]["errors"][0]

    res = (await client.post(f"/api/imports/{prev['id']}/commit", headers=auth(OWNER_TG))).json()
    assert res["added"] == 2
    assert res["available"] == {str(p["id"]): 2}
    # Повторное подтверждение ничего не добавляет.
    again = (await client.post(f"/api/imports/{prev['id']}/commit", headers=auth(OWNER_TG))).json()
    assert again == res

    # Повторная загрузка того же файла — только дубликаты.
    r = await client.post(
        "/api/imports",
        files={"file": ("tasks.xlsx", content)},
        data={"product_id": str(p["id"])},
        headers=auth(OWNER_TG),
    )
    s2 = r.json()["summary"]
    assert s2["ok"] == 0 and s2["duplicates"] == 3


async def test_import_requires_product_and_permission(client):
    await owner_ready(client)
    emp = await make_active(client, 34)
    content = xlsx([["1"]], ["ID задания"])
    r = await client.post(
        "/api/imports", files={"file": ("t.xlsx", content)}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 400 and r.json()["code"] == "no_product"
    r = await client.post("/api/imports", files={"file": ("t.xlsx", content)}, headers=auth(34))
    assert r.status_code == 403
    r = await client.post(
        "/api/imports", files={"file": ("t.xlsx", b"not excel")}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 400 and r.json()["code"] == "bad_file"
    assert emp


async def test_two_employees_never_get_the_same_task(client, session):
    await owner_ready(client)
    ids = [await make_active(client, 100 + i, f"Имя{chr(1072 + i)}", "Тестов") for i in range(6)]
    p = await ready_product(client, ids, n_tasks=3)
    results = await asyncio.gather(
        *(client.post(f"/api/my/products/{p['id']}/take", headers=auth(100 + i)) for i in range(6))
    )
    ok = [r.json() for r in results if r.status_code == 200]
    assert len(ok) == 3
    assert len({t["id"] for t in ok}) == 3
    assert {r.json()["code"] for r in results if r.status_code != 200} == {"pool_empty"}


async def test_active_limit_with_parallel_requests(client, session):
    await owner_ready(client)
    emp = await make_active(client, 40)
    p = await ready_product(client, [emp], n_tasks=5, active_task_limit=2)
    results = await asyncio.gather(
        *(client.post(f"/api/my/products/{p['id']}/take", headers=auth(40)) for _ in range(5))
    )
    assert sum(r.status_code == 200 for r in results) == 2
    assert {r.json()["code"] for r in results if r.status_code != 200} == {"task_limit"}
    active = await session.scalar(
        select(func.count()).where(Task.assignee_id == emp, Task.status == TaskStatus.ACTIVE)
    )
    assert active == 2


async def test_idempotency_key_returns_same_task(client):
    await owner_ready(client)
    emp = await make_active(client, 41)
    p = await ready_product(client, [emp], n_tasks=3, active_task_limit=3)
    h = {**auth(41), "Idempotency-Key": "abc-1"}
    t1 = (await client.post(f"/api/my/products/{p['id']}/take", headers=h)).json()
    t2 = (await client.post(f"/api/my/products/{p['id']}/take", headers=h)).json()
    assert t1["id"] == t2["id"]
    mine = (await client.get("/api/my/tasks", headers=auth(41))).json()
    assert len(mine) == 1


async def test_hidden_product_and_no_access(client):
    await owner_ready(client)
    emp = await make_active(client, 42)
    other = await make_active(client, 43)
    p = await ready_product(client, [emp], n_tasks=2)
    r = await client.post(f"/api/my/products/{p['id']}/take", headers=auth(43))
    assert r.status_code == 403 and r.json()["code"] == "no_product_access"
    await client.put(
        f"/api/products/{p['id']}/status", json={"status": "hidden"}, headers=auth(OWNER_TG)
    )
    r = await client.post(f"/api/my/products/{p['id']}/take", headers=auth(42))
    assert r.status_code == 409 and r.json()["code"] == "product_inactive"
    assert other


async def test_gender_restriction(client):
    await owner_ready(client)
    man = await make_active(client, 44)
    woman = await make_active(client, 45)
    await client.put(f"/api/users/{man}/gender", json={"gender": "m"}, headers=auth(OWNER_TG))
    await client.put(f"/api/users/{woman}/gender", json={"gender": "f"}, headers=auth(OWNER_TG))
    p = await ready_product(client, [man, woman], n_tasks=0)
    await import_tasks(client, p["id"], ["W1"], **{"Пол": ["ж"]})
    r = await client.post(f"/api/my/products/{p['id']}/take", headers=auth(44))
    assert r.status_code == 409 and r.json()["code"] == "pool_empty"
    r = await client.post(f"/api/my/products/{p['id']}/take", headers=auth(45))
    assert r.status_code == 200 and r.json()["external_id"] == "W1"


async def test_product_network_area(client):
    await owner_ready(client)
    net = (
        await client.post("/api/networks", json={"name": "Север"}, headers=auth(OWNER_TG))
    ).json()
    inside = await make_active(client, 46)
    outside = await make_active(client, 47)
    await client.put(
        f"/api/users/{inside}/networks", json={"network_ids": [net["id"]]}, headers=auth(OWNER_TG)
    )
    p = await ready_product(client, [inside, outside], n_tasks=2)
    await client.put(
        f"/api/products/{p['id']}/networks", json={"ids": [net["id"]]}, headers=auth(OWNER_TG)
    )
    assert (
        await client.post(f"/api/my/products/{p['id']}/take", headers=auth(47))
    ).status_code == 403
    assert (
        await client.post(f"/api/my/products/{p['id']}/take", headers=auth(46))
    ).status_code == 200


async def test_return_to_pool(client):
    await owner_ready(client)
    emp = await make_active(client, 48)
    p = await ready_product(client, [emp], n_tasks=1)
    t = (await client.post(f"/api/my/products/{p['id']}/take", headers=auth(48))).json()
    r = await client.post(
        f"/api/tasks/{t['id']}/return-to-pool",
        json={"reason": "Ошибка в ссылке"},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pool" and r.json()["assignee_id"] is None
    detail = (await client.get(f"/api/tasks/{t['id']}", headers=auth(OWNER_TG))).json()
    assert [e["kind"] for e in detail["events"]] == ["assigned", "returned_to_pool"]
    # Сотрудник без tasks.manage не может возвращать задания.
    t = (await client.post(f"/api/my/products/{p['id']}/take", headers=auth(48))).json()
    r = await client.post(f"/api/tasks/{t['id']}/return-to-pool", json={}, headers=auth(48))
    assert r.status_code == 403
