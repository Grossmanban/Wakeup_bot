"""Цели и соревнования, рефералы, кандидаты, обучение (ТЗ 10, 12)."""

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin, str_enum


class GoalStatus(enum.StrEnum):
    ACTIVE = "active"
    HIDDEN = "hidden"
    ARCHIVED = "archived"
    DELETED = "deleted"


class Goal(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "goals"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # goal | competition
    # individual — у каждого своя цель; team — общий объём участников.
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="individual")
    # {"type": user|users|network|coach_team|instructor_team|custom, "ids": [...]}
    participants_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    product_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    target_units: Mapped[int | None] = mapped_column(Integer)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    # period — объём за период; deadline — успеть к дате (для показа срока).
    deadline_type: Mapped[str] = mapped_column(String(16), nullable=False, default="period")
    # participants — видят участники; all — все сотрудники; managers — только руководители.
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="participants")
    # approved — единицы, допущенные к оплате; completed — все выполненные.
    counting: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")
    status: Mapped[GoalStatus] = mapped_column(
        str_enum(GoalStatus, "goal_status"), nullable=False, default=GoalStatus.ACTIVE
    )
    freeze_participants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Соревнования
    mode: Mapped[str | None] = mapped_column(String(16))  # race | max
    ranking: Mapped[str] = mapped_column(String(16), nullable=False, default="individual")
    prizes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    exclusions: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    tie_break: Mapped[str] = mapped_column(String(16), nullable=False, default="earliest")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class GoalParticipant(Base):
    """Состав участников, зафиксированный на старте."""

    __tablename__ = "goal_participants"

    goal_id: Mapped[int] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class CandidateStatus(enum.StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    REVIEWING = "reviewing"
    NEEDS_INFO = "needs_info"
    APPROVED = "approved"
    REJECTED = "rejected"
    TRAINING = "training"


class Candidate(IdMixin, CreatedAtMixin, Base):
    """Анкета кандидата (ТЗ 12.2)."""

    __tablename__ = "candidates"
    __table_args__ = (
        Index(
            "uq_candidates_open",
            "user_id",
            unique=True,
            postgresql_where=text("status NOT IN ('rejected', 'training')"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[CandidateStatus] = mapped_column(
        str_enum(CandidateStatus, "candidate_status"), nullable=False, default=CandidateStatus.DRAFT
    )
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(200))
    days_per_week: Mapped[int | None] = mapped_column(Integer)
    can_train: Mapped[bool | None] = mapped_column(Boolean)
    skills: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    truthful: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pd_consent_version: Mapped[str | None] = mapped_column(String(32))
    pd_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    responsible_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str | None] = mapped_column(Text)  # внутренний комментарий
    reason: Mapped[str | None] = mapped_column(Text)  # причина отказа
    info_request: Mapped[str | None] = mapped_column(Text)  # что уточнить
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Самоотчёт кандидата о контакте (ТЗ 12.3): wrote | cannot_contact
    contact_status: Mapped[str | None] = mapped_column(String(16))
    contact_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contact_message_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CandidateEvent(IdMixin, Base):
    __tablename__ = "candidate_events"

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id"), nullable=False, index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReferralEvent(IdMixin, Base):
    """Только события, реально полученные ботом или сервером (ТЗ 12.1)."""

    __tablename__ = "referral_events"
    __table_args__ = (Index("ix_referral_events_referrer", "referrer_id", "kind"),)

    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # visit | form_started | submitted | approved | rejected | training | training_done | bonus
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TrainingSession(IdMixin, CreatedAtMixin, Base):
    """Назначенное обучение (ТЗ 12.4)."""

    __tablename__ = "training_sessions"

    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id"), nullable=False, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    place: Mapped[str] = mapped_column(String(300), nullable=False)
    contact_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    # scheduled | confirmed | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class TrainingConfirmation(IdMixin, CreatedAtMixin, Base):
    """Подтверждённый администратором учебный объём. Действует последняя запись (ТЗ 12.5)."""

    __tablename__ = "training_confirmations"
    __table_args__ = (CheckConstraint("confirmed_units >= 0", name="non_negative"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    confirmed_units: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_units: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
