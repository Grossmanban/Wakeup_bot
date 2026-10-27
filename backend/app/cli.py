"""Служебные команды для сервера (вызываются из deploy/owners.sh).

python -m app.cli owners                 — владельцы в базе
python -m app.cli whois <tg_id>          — кто это (имя, @username, роль)
python -m app.cli recent                 — последние, кто открывал бота или приложение
python -m app.cli resolve <@username>    — Telegram ID по username (из базы и журнала доступа)
python -m app.cli promote-owner <tg_id>  — сделать владельцем сразу, не дожидаясь входа
python -m app.cli demote-owner <tg_id>   — снять роль владельца (станет администратором)
"""

import asyncio
import sys

from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AccessAttempt, AccountStatus, Role, User
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


def _describe(u: User | None, a: AccessAttempt | None, tg_id: int) -> str:
    if u is None and a is None:
        return f"{tg_id}: в базе нет — человек ещё не открывал бота"
    name = (u.full_name if u else None) or " ".join(
        x for x in ((a.first_name, a.last_name) if a else ()) if x
    )
    username = (u.username if u else None) or (a.username if a else None)
    role = u.role.value if u else "нет аккаунта"
    return f"{tg_id}: {name or '—'}{f' (@{username})' if username else ''}, роль: {role}"


async def whois(tg_id: int) -> None:
    async with get_sessionmaker()() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg_id))
        a = await s.get(AccessAttempt, tg_id)
    print(_describe(u, a, tg_id))


async def resolve(query: str) -> None:
    name = query.lstrip("@").lower()
    async with get_sessionmaker()() as s:
        ids = set(
            (await s.execute(select(User.telegram_id).where(func.lower(User.username) == name)))
            .scalars()
            .all()
        ) | set(
            (
                await s.execute(
                    select(AccessAttempt.telegram_id).where(
                        func.lower(AccessAttempt.username) == name
                    )
                )
            )
            .scalars()
            .all()
        )
    if len(ids) != 1:
        print(
            f"@{name}: не найден — пусть откроет бота, или укажите Telegram ID цифрами"
            if not ids
            else f"@{name}: найдено несколько — укажите Telegram ID цифрами",
            file=sys.stderr,
        )
        sys.exit(1)
    print(ids.pop())


async def promote(tg_id: int) -> None:
    async with get_sessionmaker()() as s:
        u = await s.scalar(select(User).where(User.telegram_id == tg_id).with_for_update())
        if u is None:
            print(f"{tg_id}: станет владельцем при первом открытии бота или приложения")
            return
        if u.role != Role.OWNER or u.status != AccountStatus.ACTIVE:
            old = {"role": u.role, "status": u.status}
            u.role, u.status = Role.OWNER, AccountStatus.ACTIVE
            audit.record(
                s,
                category=Category.USERS,
                action="owner.bootstrap",
                actor_id=None,
                target_user_id=u.id,
                object_type="user",
                object_id=u.id,
                old=old,
                new={"role": u.role, "status": u.status},
                reason="Добавлен в OWNER_TELEGRAM_IDS (deploy/owners.sh)",
            )
            await s.commit()
        print(f"{tg_id}: {u.full_name or u.username or '—'} — теперь владелец")


async def recent() -> None:
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(AccessAttempt, User)
                .outerjoin(User, User.telegram_id == AccessAttempt.telegram_id)
                .order_by(AccessAttempt.last_at.desc())
                .limit(15)
            )
        ).all()
        seen = {a.telegram_id for a, _ in rows}
        users = (
            (
                await s.execute(
                    select(User)
                    .where(User.telegram_id.not_in(seen or {0}))
                    .order_by(User.created_at.desc())
                    .limit(15 - len(rows))
                )
            )
            .scalars()
            .all()
        )
    for a, u in rows:
        print(_describe(u, a, a.telegram_id))
    for u in users:
        print(_describe(u, None, u.telegram_id))


def main() -> None:
    match sys.argv[1:]:
        case ["owners"]:
            asyncio.run(owners())
        case ["demote-owner", tg] if tg.isdigit():
            asyncio.run(demote(int(tg)))
        case ["promote-owner", tg] if tg.isdigit():
            asyncio.run(promote(int(tg)))
        case ["whois", tg] if tg.isdigit():
            asyncio.run(whois(int(tg)))
        case ["recent"]:
            asyncio.run(recent())
        case ["resolve", q]:
            asyncio.run(resolve(q))
        case _:
            print(__doc__)
            sys.exit(2)


if __name__ == "__main__":
    main()
