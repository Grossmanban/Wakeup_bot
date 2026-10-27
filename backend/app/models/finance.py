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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin, str_enum


class EntryKind(enum.StrEnum):
    EARNING = "earning"  # + заработок за допущенные к оплате единицы
    EARNING_REVERSAL = "earning_reversal"  # − отмена заработка при изменении решения модерации
    PENALTY = "penalty"  # − штраф
    BONUS = "bonus"  # + ручной или автоматический бонус
    BONUS_REVERSAL = "bonus_reversal"  # − отмена бонуса
    REFERRAL_BONUS = "referral_bonus"  # + реферальный бонус
    ADJUSTMENT = "adjustment"  # ± ручная корректировка
    OFFSET = "offset"  # ± взаимозачёт
    LAZY_ADJUSTMENT = "lazy_adjustment"  # − разница при оплате по сниженной ставке


class LedgerEntry(IdMixin, CreatedAtMixin, Base):
    """Финансовый журнал (ТЗ 11.1). Записи не изменяются: исправление — новая запись.

    amount_kop со знаком: «+» — начислено сотруднику, «−» — его обязательство.
    Положительные записи попадают в выплату целиком (payout_id), отрицательные гасятся
    частями из выплат (payout_allocations).
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        Index("ix_ledger_user_time", "user_id", "occurred_at"),
        Index("ix_ledger_task", "task_id"),
        Index("ix_ledger_unpaid", "user_id", "payout_id"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[EntryKind] = mapped_column(str_enum(EntryKind, "entry_kind"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    units: Mapped[int | None] = mapped_column(Integer)
    rate_kop: Mapped[int | None] = mapped_column(BigInteger)
    amount_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Когда произошло событие (для периодов): для заработка — время выполнения задания.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    source_entry_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_entries.id"))
    payout_id: Mapped[int | None] = mapped_column(ForeignKey("payouts.id"))
    bonus_rule_id: Mapped[int | None] = mapped_column(ForeignKey("bonus_rules.id"))
    # Ключ идемпотентности: одна и та же операция не проводится дважды.
    dedup_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


class PayoutStatus(enum.StrEnum):
    CONFIRMED = "confirmed"


class Payout(IdMixin, CreatedAtMixin, Base):
    """Подтверждённая выплата с неизменяемым снимком расчёта (ТЗ 11.6)."""

    __tablename__ = "payouts"
    __table_args__ = (CheckConstraint("amount_kop >= 0", name="non_negative"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    amount_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[PayoutStatus] = mapped_column(
        str_enum(PayoutStatus, "payout_status"), nullable=False, default=PayoutStatus.CONFIRMED
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


class PayoutAllocation(IdMixin, Base):
    """Сколько из долга (отрицательной записи) погашено конкретной выплатой."""

    __tablename__ = "payout_allocations"
    __table_args__ = (CheckConstraint("amount_kop > 0", name="positive"),)

    payout_id: Mapped[int] = mapped_column(ForeignKey("payouts.id"), nullable=False, index=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_entries.id"), nullable=False, index=True
    )
    amount_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)


class BonusRule(IdMixin, CreatedAtMixin, Base):
    """Автоматический бонус (ТЗ 11.4)."""

    __tablename__ = "bonus_rules"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # [{"product_id": int, "min_units": int}]
    conditions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    min_quality: Mapped[int | None] = mapped_column(Integer)  # %, None — без условия
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class LazyRule(IdMixin, CreatedAtMixin, Base):
    """«Налог на лень» (ТЗ 11.5)."""

    __tablename__ = "lazy_rules"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    min_daily_units: Mapped[int] = mapped_column(Integer, nullable=False)
    reduced_rate_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Роли, к которым применяется правило (пусто — ко всем), и отдельные сотрудники.
    roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    user_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class LazyExemption(Base):
    """Персональное исключение из «налога на лень» (по умолчанию выключено)."""

    __tablename__ = "lazy_exemptions"

    rule_id: Mapped[int] = mapped_column(
        ForeignKey("lazy_rules.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LazyUnits(IdMixin, CreatedAtMixin, Base):
    """Единицы по сниженной ставке за день, когда не выполнен минимум.

    Учитываются отдельно от штрафов и долгов; при выплате первыми оплачиваются по
    сниженной ставке, остаток переносится (ТЗ 11.5).
    """

    __tablename__ = "lazy_units"
    __table_args__ = (
        UniqueConstraint("rule_id", "user_id", "day", name="uq_lazy_units_day"),
        CheckConstraint("units >= 0", name="non_negative"),
    )

    rule_id: Mapped[int] = mapped_column(ForeignKey("lazy_rules.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    done_units: Mapped[int] = mapped_column(Integer, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)  # недобор
    reduced_rate_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)


class LazyConsumption(IdMixin, Base):
    """Сколько накопленных единиц по сниженной ставке учтено в выплате."""

    __tablename__ = "lazy_consumptions"
    __table_args__ = (CheckConstraint("units > 0", name="positive"),)

    payout_id: Mapped[int] = mapped_column(ForeignKey("payouts.id"), nullable=False, index=True)
    lazy_id: Mapped[int] = mapped_column(ForeignKey("lazy_units.id"), nullable=False, index=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    normal_rate_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reduced_rate_kop: Mapped[int] = mapped_column(BigInteger, nullable=False)


class JobRun(Base):
    """Отметка о выполненной фоновой задаче: повторный запуск с тем же ключом пропускается."""

    __tablename__ = "job_runs"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class DownloadToken(Base):
    """Короткоживущая ссылка на выгрузку: файл скачивается без заголовка авторизации."""

    __tablename__ = "download_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
