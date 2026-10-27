import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin, str_enum


class Role(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    COACH = "coach"
    INSTRUCTOR = "instructor"
    EMPLOYEE = "employee"


class AccountStatus(enum.StrEnum):
    # Нажал /start или открыл Mini App, но ещё не подал заявку.
    UNREGISTERED = "unregistered"
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    DISABLED = "disabled"
    BLOCKED = "blocked"


class RequestStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class User(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    # Имя из профиля Telegram — только для справки, может меняться.
    tg_first_name: Mapped[str | None] = mapped_column(String(128))
    tg_last_name: Mapped[str | None] = mapped_column(String(128))
    # Подтверждённые администрацией фамилия и имя.
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))

    role: Mapped[Role] = mapped_column(
        str_enum(Role, "role"), nullable=False, default=Role.EMPLOYEE
    )
    status: Mapped[AccountStatus] = mapped_column(
        str_enum(AccountStatus, "account_status"),
        nullable=False,
        default=AccountStatus.UNREGISTERED,
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    # "m" / "f" / NULL — для заданий с ограничением по полу. Задаёт владелец.
    gender: Mapped[str | None] = mapped_column(String(1))
    # Доступ к Mini App отделён от состояния аккаунта (ТЗ 2.3).
    app_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Рефералы и обучение (ТЗ 12)
    referral_code: Mapped[str | None] = mapped_column(String(16), unique=True)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # none — не стажёр; training — проходит обучение; completed — обучение подтверждено.
    training_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )
    training_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Дата третьего рабочего дня: с неё человек — действующий сотрудник, а не новичок (ТЗ 15.3).
    became_regular_on: Mapped[date | None] = mapped_column(Date)

    # Может ли бот писать человеку первым: он нажимал /start и не блокировал бота.
    bot_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bot_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def can_message(self) -> bool:
        """Бот может написать первым (по данным последних событий от Telegram)."""
        return self.bot_started_at is not None and self.bot_blocked_at is None

    @property
    def full_name(self) -> str:
        parts = [p for p in (self.last_name, self.first_name) if p]
        if parts:
            return " ".join(parts)
        tg = [p for p in (self.tg_first_name, self.tg_last_name) if p]
        return " ".join(tg) or f"id{self.telegram_id}"


class RegistrationRequest(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "registration_requests"
    __table_args__ = (
        # Одна открытая заявка на человека.
        Index(
            "uq_registration_requests_pending",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        str_enum(RequestStatus, "request_status"), nullable=False, default=RequestStatus.PENDING
    )
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class NameChangeRequest(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "name_change_requests"
    __table_args__ = (
        Index(
            "uq_name_change_requests_pending",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    old_first_name: Mapped[str | None] = mapped_column(String(64))
    old_last_name: Mapped[str | None] = mapped_column(String(64))
    new_first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    new_last_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        str_enum(RequestStatus, "request_status"), nullable=False, default=RequestStatus.PENDING
    )
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class UserPermission(IdMixin, Base):
    """Разрешение, выданное владельцем. network_id = NULL — без ограничения по сети."""

    __tablename__ = "user_permissions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "permission",
            "network_id",
            name="uq_user_permissions_grant",
            postgresql_nulls_not_distinct=True,
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id"))
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
