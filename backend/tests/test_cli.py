"""Служебные команды deploy/owners.sh."""

from sqlalchemy import select

from app import cli
from app.models import AuditEvent, Role, User
from tests.helpers import OWNER_TG, owner_ready


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
