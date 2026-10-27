"""Служебные команды deploy/owners.sh."""

from sqlalchemy import select

from app import cli
from app.models import AuditEvent, Role, User
from tests.helpers import OWNER_TG, auth, owner_ready


async def test_owners_list_and_demote(client, session, capsys):
    await owner_ready(client)
    await cli.owners()
    assert str(OWNER_TG) in capsys.readouterr().out

    await cli.demote(OWNER_TG)
    u = await session.scalar(
        select(User).where(User.telegram_id == OWNER_TG).execution_options(populate_existing=True)
    )
    assert u.role == Role.ADMIN
    assert await session.scalar(select(AuditEvent).where(AuditEvent.action == "owner.removed"))

    await cli.demote(OWNER_TG)  # повтор ничего не меняет
    assert "не владелец" in capsys.readouterr().out


async def test_whois_resolve_promote_recent(client, session, capsys):
    from app.db import get_sessionmaker
    from app.security.telegram_auth import TelegramUser
    from app.services import entry
    from tests.helpers import make_active

    await owner_ready(client)
    uid = await make_active(client, 600, "Саша", "Ливанов")
    async with get_sessionmaker()() as s:
        u = await s.get(User, uid)
        u.username = "Sashka"
        await s.commit()
        await entry.handle_start(s, TelegramUser(600, "Саша", None, "Sashka"), "")
    capsys.readouterr()

    await cli.resolve("@sashka")
    assert capsys.readouterr().out.strip() == "600"
    await cli.whois(600)
    out = capsys.readouterr().out
    assert "Ливанов Саша" in out and "@Sashka" in out and "employee" in out
    await cli.recent()
    assert "600" in capsys.readouterr().out

    await cli.promote(600)
    u = await session.scalar(
        select(User).where(User.telegram_id == 600).execution_options(populate_existing=True)
    )
    assert u.role == Role.OWNER
    me = (await client.get("/api/me", headers=auth(600))).json()
    assert me["is_owner"] is True

    import pytest

    with pytest.raises(SystemExit):
        await cli.resolve("@nobody")
