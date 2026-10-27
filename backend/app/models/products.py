import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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

from app.models.base import Base, CreatedAtMixin, IdMixin, str_enum
from app.models.users import Role


class ProductStatus(enum.StrEnum):
    ACTIVE = "active"
    HIDDEN = "hidden"  # новые задания не выдаются, история сохраняется
    ARCHIVED = "archived"
    DELETED = "deleted"  # удалён из интерфейса, данные и история остаются


class OcrRule(enum.StrEnum):
    CONTAINS = "contains"  # ожидаемый текст содержится в распознанном
    EXACT = "exact"  # совпадает целиком (без учёта регистра, пробелов и знаков)
    FUZZY = "fuzzy"  # похожесть не ниже порога


class Product(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "products"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProductStatus] = mapped_column(
        str_enum(ProductStatus, "product_status"), nullable=False, default=ProductStatus.ACTIVE
    )
    active_task_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    instructions: Mapped[str | None] = mapped_column(Text)
    # [{"title": str, "url": str}]
    links: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    materials: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # Коды полей задания, обязательных при импорте: link, expected_text, rating, ...
    required_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Ограничение по полу на уровне продукта: "m" / "f" / NULL.
    gender: Mapped[str | None] = mapped_column(String(1))
    expected_text_default: Mapped[str | None] = mapped_column(Text)

    # Этапы выполнения (ТЗ 6):
    # [{"title", "instructions", "checklist", "min_seconds", "photo": none/optional/required}]
    flow: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    final_photo_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timer_reset_reason_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allow_return_for_fix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # OCR (ТЗ 7.1)
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ocr_rule: Mapped[OcrRule] = mapped_column(
        str_enum(OcrRule, "ocr_rule"), nullable=False, default=OcrRule.CONTAINS
    )
    ocr_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=85)
    ocr_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # Объём стажировки в единицах (ТЗ 12.5).
    training_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Минимум проверенных единиц, ниже которого статистика качества помечается как малая выборка.
    min_sample_units: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TaskType(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "task_types"
    __table_args__ = (UniqueConstraint("product_id", "name", name="uq_task_types_product_name"),)

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Как этот тип называется в Excel (без учёта регистра).
    excel_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Сколько оплачиваемых единиц даёт одно задание этого типа.
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProductRate(IdMixin, CreatedAtMixin, Base):
    """История ставок за единицу. Действует последняя запись; amount_kop = NULL — ставка снята.

    Ровно одно из role / user_id: ставка роли или индивидуальная ставка сотрудника.
    """

    __tablename__ = "product_rates"
    __table_args__ = (
        CheckConstraint("(role IS NULL) <> (user_id IS NULL)", name="role_xor_user"),
        CheckConstraint("amount_kop IS NULL OR amount_kop >= 0", name="non_negative"),
        Index("ix_product_rates_lookup", "product_id", "user_id", "role", "id"),
    )

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    role: Mapped[Role | None] = mapped_column(str_enum(Role, "rate_role"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    amount_kop: Mapped[int | None] = mapped_column(BigInteger)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ProductAccess(Base):
    __tablename__ = "product_access"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductNetwork(Base):
    """Сети, сотрудникам которых выдаются задания продукта. Пусто — всем сетям."""

    __tablename__ = "product_networks"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    network_id: Mapped[int] = mapped_column(
        ForeignKey("networks.id", ondelete="CASCADE"), primary_key=True
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class IdempotencyKey(Base):
    """Ответ на уже выполненный запрос: повтор с тем же ключом не выполняет действие снова."""

    __tablename__ = "idempotency_keys"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
