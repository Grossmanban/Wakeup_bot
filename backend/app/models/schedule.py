import enum
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin, str_enum


class WorkPoint(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "work_points"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    # Сколько человек одновременно; NULL — без ограничения.
    capacity: Mapped[int | None] = mapped_column(Integer)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ShiftStatus(enum.StrEnum):
    PENDING = "pending"  # ожидает подтверждения (смена на сегодня)
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


ACTIVE_SHIFT = (ShiftStatus.PENDING, ShiftStatus.CONFIRMED)


class Shift(IdMixin, CreatedAtMixin, Base):
    """Смена в графике (ТЗ 9). Даты — календарные дни по МСК, время — МСК."""

    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="time_order"),
        # Одна действующая смена на человека в день.
        Index(
            "uq_shifts_user_day_active",
            "user_id",
            "day",
            unique=True,
            postgresql_where=text("status IN ('pending', 'confirmed')"),
        ),
        Index("ix_shifts_day_point", "day", "point_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    point_id: Mapped[int] = mapped_column(ForeignKey("work_points.id"), nullable=False)
    status: Mapped[ShiftStatus] = mapped_column(
        str_enum(ShiftStatus, "shift_status"), nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ShiftPlan(Base):
    """Плановый объём смены по продукту."""

    __tablename__ = "shift_plans"
    __table_args__ = (CheckConstraint("units > 0", name="positive"),)

    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shifts.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False)


class DayOff(Base):
    __tablename__ = "days_off"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_days_off"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
