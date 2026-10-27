"""Рассылки (ТЗ 13)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin


class Broadcast(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "broadcasts"

    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)  # Telegram HTML (разрешённые теги)
    photo_path: Mapped[str | None] = mapped_column(String(255))
    # {"all": bool, "user_ids": [...], "network_ids": [...], "coach_id", "instructor_id",
    #  "working_today": bool | None, "scheduled_tomorrow": bool | None, "no_status_tomorrow": bool}
    audience: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    require_ack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_replies: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")  # draft | sent
    recipients_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BroadcastRecipient(IdMixin, Base):
    __tablename__ = "broadcast_recipients"
    __table_args__ = (UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipient"),)

    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # queued | sent | failed. «sent» — Telegram подтвердил отправку, а не «прочитано».
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Только кнопка «Я ознакомился» подтверждает прочтение (ТЗ 13).
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BroadcastReply(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "broadcast_replies"

    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    media_kind: Mapped[str | None] = mapped_column(String(16))  # photo | document | video | voice
    media_file_id: Mapped[str | None] = mapped_column(String(255))
