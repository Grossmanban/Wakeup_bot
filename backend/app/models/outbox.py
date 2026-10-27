import enum
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin, str_enum


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"  # Telegram API подтвердил отправку (не значит «прочитано»).
    FAILED = "failed"  # окончательная ошибка, повторов не будет


class TelegramOutbox(IdMixin, CreatedAtMixin, Base):
    """Очередь исходящих сообщений Telegram.

    Бизнес-операция пишет сюда запись в своей же транзакции, фоновый обработчик
    отправляет. Сбой Telegram не откатывает данные и не теряет сообщение (ТЗ 0.1 п.11).
    """

    __tablename__ = "telegram_outbox"
    __table_args__ = (Index("ix_telegram_outbox_due", "status", "next_attempt_at"),)

    # Ключ идемпотентности: повтор той же операции не создаёт второе сообщение.
    dedup_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="sendMessage")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    related_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    status: Mapped[OutboxStatus] = mapped_column(
        str_enum(OutboxStatus, "outbox_status"), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
