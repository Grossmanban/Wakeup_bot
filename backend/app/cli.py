"""Служебные команды для сервера (вызываются из deploy/owners.sh).

python -m app.cli owners                 — владельцы в базе
python -m app.cli demote-owner <tg_id>   — снять роль владельца (станет администратором)
"""

import asyncio
import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Role, User
from app.services import audit
from app.services.audit import Category


async def owners() -> None:
    env = set(get_settings().owner_telegram_ids)
    async with get_sessionmaker()() as s:
        users = (await s.execute(select(User).where(User.role == Role.OWNER))).scalars().all()
    seen = set()
    for u in users:
        seen.add(u.telegram_id)
        mark = "" if u.telegram_id in env else "  (нет в .env — будет снят)"
        print(f"{u.telegram_id}  {u.full_name or u.username or '—'}{mark}")
    for tg in sorted(env - seen):
        print(f"{tg}  — ещё не открывал приложение (станет владельцем при первом входе)")


async def demote(tg_id: int) -> None:
    async with get_sessionmaker()() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg_id).with_for_update())
        if u is None or u.role != Role.OWNER:
            print("не владелец — ничего не изменено")
            return
        u.role = Role.ADMIN
        audit.record(
            s,
            category=Category.USERS,
            action="owner.removed",
            actor_id=None,
            target_user_id=u.id,
            object_type="user",
            object_id=u.id,
            old={"role": Role.OWNER},
            new={"role": Role.ADMIN},
            reason="Telegram ID удалён из OWNER_TELEGRAM_IDS (deploy/owners.sh)",
        )
        await s.commit()
        print(f"{tg_id}: роль владельца снята, теперь администратор без разрешений")


def main() -> None:
    match sys.argv[1:]:
        case ["owners"]:
            asyncio.run(owners())
        case ["demote-owner", tg] if tg.isdigit():
            asyncio.run(demote(int(tg)))
        case _:
            print(__doc__)
            sys.exit(2)


if __name__ == "__main__":
    main()
