from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.errors import AppError, Forbidden
from app.models import AccountStatus, User
from app.security.telegram_auth import InitDataError, validate_init_data
from app.services import users as users_svc
from app.services.access import Actor, load_actor

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Пользователь из подписанных Telegram initData: заголовок `Authorization: tma <initData>`.

    Telegram ID берётся только из проверенной подписи — подменить его клиентом нельзя.
    """
    settings = get_settings()
    if not authorization or not authorization.startswith("tma "):
        raise AppError("unauthorized", "Откройте приложение из Telegram", 401)
    try:
        data = validate_init_data(authorization[4:], settings.bot_token, settings.init_data_max_age)
    except InitDataError as e:
        raise AppError("unauthorized", f"Не удалось проверить вход: {e}", 401) from e
    user = await users_svc.upsert_from_telegram(session, data.user)
    await session.commit()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def active_actor(session: SessionDep, user: CurrentUser) -> Actor:
    """Работать с приложением могут только активные аккаунты с доступом к Mini App."""
    if user.status in users_svc.STATUS_MESSAGES:
        raise Forbidden(f"account_{user.status.value}", users_svc.STATUS_MESSAGES[user.status])
    if user.status != AccountStatus.ACTIVE:
        raise Forbidden("not_registered", "Регистрация ещё не одобрена")
    if not user.app_access:
        raise Forbidden("no_app_access", users_svc.NO_APP_ACCESS_MESSAGE)
    return await load_actor(session, user)


ActorDep = Annotated[Actor, Depends(active_actor)]
