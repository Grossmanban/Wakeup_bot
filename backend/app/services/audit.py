from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent


class Category(StrEnum):
    """Совпадает с темами Telegram-архива аудита (ТЗ 14)."""

    USERS = "users"  # пользователи и доступы
    PENALTIES = "penalties"
    BONUSES = "bonuses"
    TASKS = "tasks"
    PHOTOS = "photos"
    SCHEDULE = "schedule"
    FINANCE = "finance"
    SETTINGS = "settings"
    GROUPS = "groups"
    BROADCASTS = "broadcasts"
    SYSTEM = "system"  # ошибки доставки и системные события


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    return str(v)


def record(
    session: AsyncSession,
    *,
    category: Category,
    action: str,
    actor_id: int | None,
    target_user_id: int | None = None,
    object_type: str | None = None,
    object_id: Any = None,
    old: Any = None,
    new: Any = None,
    reason: str | None = None,
    result: str = "success",
) -> AuditEvent:
    """Добавить запись аудита в текущую транзакцию: она сохранится вместе с изменением."""
    ev = AuditEvent(
        category=category.value,
        action=action,
        actor_id=actor_id,
        target_user_id=target_user_id,
        object_type=object_type,
        object_id=None if object_id is None else str(object_id),
        old_value=_jsonable(old),
        new_value=_jsonable(new),
        reason=reason,
        result=result,
    )
    session.add(ev)
    return ev
