"""Защита от повторных запросов (двойное нажатие, повтор после обрыва связи).

Клиент передаёт заголовок Idempotency-Key. Если запрос с этим ключом уже выполнен,
возвращается сохранённый ответ, а действие не повторяется.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict
from app.models import IdempotencyKey


async def lookup(session: AsyncSession, user_id: int, key: str | None, scope: str) -> Any | None:
    if not key:
        return None
    row = await session.scalar(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
    )
    if row is None:
        return None
    if row.scope != scope:
        raise Conflict(
            "idempotency_key_reused", "Ключ запроса уже использован для другого действия"
        )
    return row.response


async def remember(
    session: AsyncSession, user_id: int, key: str | None, scope: str, response: Any
) -> None:
    """Сохранить ответ в той же транзакции, что и само действие."""
    if not key:
        return
    await session.execute(
        insert(IdempotencyKey)
        .values(user_id=user_id, key=key[:128], scope=scope, response=response)
        .on_conflict_do_nothing()
    )
