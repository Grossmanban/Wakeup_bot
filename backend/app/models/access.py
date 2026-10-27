"""Скрытый режим бота: приглашения действующих сотрудников и журнал попыток доступа."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin


class StaffInvite(IdMixin, CreatedAtMixin, Base):
    """Приглашение действующего сотрудника: регистрация данных без анкеты и стажировки.

    Тип приглашения определяется записью на сервере, а не текстом ссылки. Статус
    вычисляется: отозвано → истекло → использовано (uses ≥ max_uses) → действует.
    """

    __tablename__ = "staff_invites"
    __table_args__ = (
        CheckConstraint("max_uses >= 1", name="max_uses_positive"),
        CheckConstraint("uses >= 0 AND uses <= max_uses", name="uses_range"),
    )

    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="staff")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class StaffInviteClaim(Base):
    """Человек открыл бота по приглашению (claimed) и, возможно, зарегистрировался (used)."""

    __tablename__ = "staff_invite_claims"
    __table_args__ = (UniqueConstraint("invite_id", "telegram_id", name="uq_staff_invite_claim"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invite_id: Mapped[int] = mapped_column(ForeignKey("staff_invites.id"), nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AccessAttempt(Base):
    """Сводка по Telegram ID: кто и когда взаимодействовал с ботом. Один ряд на человека."""

    __tablename__ = "access_attempts"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_source: Mapped[str | None] = mapped_column(String(80))


class AccessEvent(IdMixin, Base):
    """Одно событие: /start, сообщение, кнопка, открытие приложения, использование приглашения."""

    __tablename__ = "access_events"
    __table_args__ = (Index("ix_access_events_tg", "telegram_id", "id"),)

    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # start | message | callback | app | invite_claimed | invite_used | referral
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # ignored — бот промолчал; allowed — открыт сценарий; known — уже в системе
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    text: Mapped[str | None] = mapped_column(Text)
