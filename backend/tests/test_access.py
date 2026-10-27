"""Права, области видимости и защита от IDOR."""

from tests.helpers import OWNER_TG, auth, grant, make_active, owner_ready


async def _ids(client, tg):
    r = await client.get("/api/users?limit=200", headers=auth(tg))
    assert r.status_code == 200, r.text
    return {u["id"] for u in r.json()["items"]}


async def test_admin_has_no_rights_by_default(client):
    await owner_ready(client)
    admin = await make_active(client, 30, role="admin")
    await make_active(client, 31)

    assert await _ids(client, 30) == set()
    assert (await client.get("/api/registrations", headers=auth(30))).status_code == 403
    r = await client.put("/api/users/1/status", json={"status": "blocked"}, headers=auth(30))
    assert r.status_code == 403

    await grant(client, admin, "users.view", "registrations.review")
    assert len(await _ids(client, 30)) == 3  # владелец, сам админ, сотрудник
    assert (await client.get("/api/registrations", headers=auth(30))).status_code == 200


async def test_owner_only_actions(client):
    await owner_ready(client)
    admin = await make_active(client, 32, role="admin")
    emp = await make_active(client, 33)
    await grant(client, admin, "users.view", "users.status", "org.manage")

    r = await client.put(f"/api/users/{emp}/role", json={"role": "coach"}, headers=auth(32))
    assert r.status_code == 403
    r = await client.put(
        f"/api/users/{emp}/permissions",
        json={"grants": [{"permission": "users.view"}]},
        headers=auth(32),
    )
    assert r.status_code == 403
    r = await client.get("/api/permissions/catalog", headers=auth(32))
    assert r.status_code == 403

    # Роль владельца из приложения не выдаётся.
    r = await client.put(f"/api/users/{emp}/role", json={"role": "owner"}, headers=auth(OWNER_TG))
    assert r.status_code == 403


async def test_network_scoped_admin(client):
    await owner_ready(client)
    n1 = (await client.post("/api/networks", json={"name": "Север"}, headers=auth(OWNER_TG))).json()
    n2 = (await client.post("/api/networks", json={"name": "Юг"}, headers=auth(OWNER_TG))).json()
    admin = await make_active(client, 40, role="admin")
    a = await make_active(client, 41)
    b = await make_active(client, 42)
    for uid, net in ((a, n1["id"]), (b, n2["id"])):
        r = await client.put(
            f"/api/users/{uid}/networks", json={"network_ids": [net]}, headers=auth(OWNER_TG)
        )
        assert r.status_code == 200

    await grant(client, admin, ("users.view", n1["id"]))
    assert await _ids(client, 40) == {a}

    # Чужой сотрудник — 404, а не 403: нельзя узнать, что такой ID существует.
    assert (await client.get(f"/api/users/{b}", headers=auth(40))).status_code == 404
    assert (await client.get(f"/api/users/{a}", headers=auth(40))).status_code == 200
    assert (await client.get("/api/users/99999", headers=auth(40))).status_code == 404

    nets = (await client.get("/api/networks", headers=auth(40))).json()
    assert [n["name"] for n in nets] == ["Север"]


async def test_coach_and_instructor_see_only_their_team(client):
    await owner_ready(client)
    coach = await make_active(client, 50, role="coach")
    instr = await make_active(client, 51, role="instructor")
    instr2 = await make_active(client, 52, role="instructor")
    e1 = await make_active(client, 53)
    e2 = await make_active(client, 54)
    outsider = await make_active(client, 55)

    for sup, sub in ((coach, instr), (coach, instr2), (instr, e1), (instr2, e1), (instr, e2)):
        r = await client.post(
            "/api/supervisions",
            json={"supervisor_id": sup, "subordinate_id": sub},
            headers=auth(OWNER_TG),
        )
        assert r.status_code == 204, r.text

    # e1 состоит в двух командах, но учитывается один раз.
    r = await client.get("/api/users?limit=200", headers=auth(50))
    items = r.json()["items"]
    assert r.json()["total"] == 4
    assert sorted(u["id"] for u in items) == sorted({instr, instr2, e1, e2})

    assert await _ids(client, 51) == {e1, e2}
    assert await _ids(client, 53) == set()
    assert (await client.get(f"/api/users/{outsider}", headers=auth(50))).status_code == 404

    prof = (await client.get(f"/api/users/{e1}", headers=auth(OWNER_TG))).json()
    assert {u["id"] for u in prof["supervisors"]} == {instr, instr2}


async def test_hierarchy_rules(client):
    await owner_ready(client)
    coach = await make_active(client, 60, role="coach")
    emp = await make_active(client, 61)
    r = await client.post(
        "/api/supervisions",
        json={"supervisor_id": emp, "subordinate_id": coach},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 400
    r = await client.post(
        "/api/supervisions",
        json={"supervisor_id": coach, "subordinate_id": coach},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 400


async def test_block_and_disable(client):
    await owner_ready(client)
    emp = await make_active(client, 70)

    r = await client.put(
        f"/api/users/{emp}/status",
        json={"status": "disabled", "reason": "Отпуск"},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200
    me = (await client.get("/api/me", headers=auth(70))).json()
    assert me["screen"] == "disabled"
    assert me["message"] == "Ваш аккаунт отключён. Обратитесь к администрации для его включения"
    r = await client.post(
        "/api/me/name-change", json={"first_name": "А", "last_name": "Б"}, headers=auth(70)
    )
    assert r.status_code == 403
    assert r.json()["code"] == "account_disabled"

    await client.put(f"/api/users/{emp}/status", json={"status": "blocked"}, headers=auth(OWNER_TG))
    me = (await client.get("/api/me", headers=auth(70))).json()
    assert me["screen"] == "blocked"
    assert me["message"] == "Ваш аккаунт заблокирован"

    await client.put(f"/api/users/{emp}/status", json={"status": "active"}, headers=auth(OWNER_TG))
    assert (await client.get("/api/me", headers=auth(70))).json()["screen"] == "ready"

    # Статус владельца и собственный статус менять нельзя.
    owner_id = (await client.get("/api/me", headers=auth(OWNER_TG))).json()["user"]["id"]
    r = await client.put(
        f"/api/users/{owner_id}/status", json={"status": "blocked"}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 403


async def test_admin_with_status_permission_cannot_block_admin(client):
    await owner_ready(client)
    a1 = await make_active(client, 80, role="admin")
    a2 = await make_active(client, 81, role="admin")
    await grant(client, a1, "users.status")
    r = await client.put(f"/api/users/{a2}/status", json={"status": "blocked"}, headers=auth(80))
    assert r.status_code == 403


async def test_app_access_separate_from_status(client):
    await owner_ready(client)
    emp = await make_active(client, 90)
    r = await client.put(
        f"/api/users/{emp}/app-access", json={"enabled": False}, headers=auth(OWNER_TG)
    )
    assert r.status_code == 200
    me = (await client.get("/api/me", headers=auth(90))).json()
    assert me["user"]["status"] == "active"
    assert me["screen"] == "no_app_access"
    assert (await client.get("/api/users", headers=auth(90))).json()["code"] == "no_app_access"


async def test_history_requires_audit_permission(client):
    await owner_ready(client)
    admin = await make_active(client, 95, role="admin")
    emp = await make_active(client, 96)
    await grant(client, admin, "users.view")
    assert (await client.get(f"/api/users/{emp}/history", headers=auth(95))).status_code == 403
    await grant(client, admin, "users.view", "audit.view")
    r = await client.get(f"/api/users/{emp}/history", headers=auth(95))
    assert r.status_code == 200
    assert {e["action"] for e in r.json()} >= {"registration.submitted", "registration.approved"}


async def test_permissions_diff_is_audited(client):
    await owner_ready(client)
    admin = await make_active(client, 97, role="admin")
    await grant(client, admin, "users.view", "audit.view")
    await grant(client, admin, "users.view")
    r = await client.get(f"/api/users/{admin}/history", headers=auth(OWNER_TG))
    perm_events = [e for e in r.json() if e["action"] == "permissions.changed"]
    assert len(perm_events) == 2
    assert perm_events[0]["old_value"] == [["audit.view", None]]
    assert perm_events[0]["new_value"] == []
