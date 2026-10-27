"""Telegram-группы, членство, приглашения, маршруты фотоотчётов (ТЗ 15, 17)."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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

# Назначение группы.
PURPOSES = ("training", "main", "photoreports", "summary", "audit", "other")


class TgGroup(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "tg_groups"

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_type: Mapped[str | None] = mapped_column(String(32))
    is_forum: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    # connected — бот в группе; left — бота удалили; error — не удалось проверить.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="connected")
    bot_rights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_error: Mapped[str | None] = mapped_column(Text)
    # Свой интервал повторных приглашений; NULL — общий из настроек.
    invite_cooldown_hours: Mapped[int | None] = mapped_column(Integer)
    # Темы форума для архива аудита: категория → message_thread_id.
    topics: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)


class GroupMembership(Base):
    """Известное системе членство. «Неизвестно» ≠ «не состоит» (ТЗ 15.2)."""

    __tablename__ = "group_memberships"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("tg_groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # member | not_member | unknown
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # event | check
    detail: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GroupInvite(IdMixin, CreatedAtMixin, Base):
    """Приглашение в конкретную группу (ТЗ 15.5)."""

    __tablename__ = "group_invites"
    __table_args__ = (Index("ix_group_invites_user_group", "user_id", "group_id", "id"),)

    group_id: Mapped[int] = mapped_column(ForeignKey("tg_groups.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    invite_link: Mapped[str | None] = mapped_column(Text)
    # prepared — ссылка создана, отправка ждёт подтверждения Telegram; sent — Telegram
    # подтвердил отправку; send_failed — не отправлено; joined — вступил.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="prepared")
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PhotoRoute(IdMixin, CreatedAtMixin, Base):
    """Куда отправлять фотоотчёты: группа продукта, общая группа, сводка, доп. маршруты."""

    __tablename__ = "photo_routes"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "product_id",
            "group_id",
            name="uq_photo_routes",
            postgresql_nulls_not_distinct=True,
        ),
    )

    # product — фото продукта; general — все фото; summary — дневная сводка; extra — доп. маршрут
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("tg_groups.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PhotoDelivery(IdMixin, CreatedAtMixin, Base):
    """Отправка конкретного фото в конкретную группу (ТЗ 17.6)."""

    __tablename__ = "photo_deliveries"
    __table_args__ = (UniqueConstraint("photo_id", "group_id", name="uq_photo_deliveries"),)

    photo_id: Mapped[int] = mapped_column(ForeignKey("task_photos.id"), nullable=False)
    group_id: Mapped[int] = mapped_column(ForeignKey("tg_groups.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # queued | sent | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DayPost(IdMixin, CreatedAtMixin, Base):
    """Заголовок дня и дневные сводки в группах (ТЗ 17.3, 17.4)."""

    __tablename__ = "day_posts"
    __table_args__ = (UniqueConstraint("group_id", "day", "kind", "version", name="uq_day_posts"),)

    group_id: Mapped[int] = mapped_column(ForeignKey("tg_groups.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # header | summary
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)  # причина корректировки
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
