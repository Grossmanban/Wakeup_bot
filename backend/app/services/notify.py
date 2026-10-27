"""Уведомления ответственным: владельцу и тем, у кого есть нужное разрешение."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountStatus, Role, User, UserPermission
from app.permissions import Perm
from app.services import outbox


async def holders(session: AsyncSession, perm: Perm | None) -> list[User]:
    q = select(User).where(User.status == AccountStatus.ACTIVE)
    if perm is None:
        q = q.where(User.role == Role.OWNER)
    else:
        q = q.outerjoin(
            UserPermission,
            (UserPermission.user_id == User.id) & (UserPermission.permission == perm.value),
        ).where(or_(User.role == Role.OWNER, UserPermission.id.is_not(None)))
    return list((await session.execute(q.distinct())).scalars())


async def notify_holders(
    session: AsyncSession,
    perm: Perm | None,
    *,
    dedup_key: str,
    text: str,
    button: str | None = None,
) -> None:
    """dedup_key — общий префикс; к нему добавляется ID получателя."""
    for u in await holders(session, perm):
        await outbox.enqueue(
            session,
            dedup_key=f"{dedup_key}:to:{u.id}",
            chat_id=u.telegram_id,
            related_user_id=u.id,
            text=text,
            open_app_button=button,
        )
