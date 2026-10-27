import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
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


class TaskStatus(enum.StrEnum):
    """Статусы задания (ТЗ 6.1, 8)."""

    POOL = "pool"  # в общем пуле
    ACTIVE = "active"  # выдано сотруднику
    MANUAL_REVIEW = "manual_review"  # ожидает ручного решения
    MODERATION = "moderation"  # на модерации
    APPROVED = "approved"  # допущено к оплате
    REJECTED = "rejected"  # отклонено
    PAID = "paid"  # оплачено (вошло в подтверждённую выплату)
    CANCELLED = "cancelled"  # отменено
    ARCHIVED = "archived"  # архивировано / удалено


# Статусы, в которых задание считается выполненным сотрудником.
DONE_STATUSES = (
    TaskStatus.MODERATION,
    TaskStatus.APPROVED,
    TaskStatus.REJECTED,
    TaskStatus.PAID,
)


class Task(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # Повторный импорт не создаёт дубликаты.
        UniqueConstraint("product_id", "external_id", name="uq_tasks_product_external"),
        Index("ix_tasks_pool", "product_id", "status", "id"),
        Index("ix_tasks_assignee", "assignee_id", "status"),
        Index("ix_tasks_completed", "completed_at"),
    )

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    task_type_id: Mapped[int | None] = mapped_column(ForeignKey("task_types.id"))
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    link: Mapped[str | None] = mapped_column(Text)
    expected_text: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[str | None] = mapped_column(String(32))
    instructions: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    materials: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    gender: Mapped[str | None] = mapped_column(String(1))
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))

    status: Mapped[TaskStatus] = mapped_column(
        str_enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.POOL
    )
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Серверное время этапов (ТЗ 6). Клиентское время не используется.
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # OCR и ручная проверка (ТЗ 7)
    ocr_attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Номер попытки выполнения: растёт при возврате в пул и на исправление. Учитываются
    # только фото текущей попытки — чужие и старые фото не засчитываются.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    manual_review_reason: Mapped[str | None] = mapped_column(Text)

    # Модерация (ТЗ 8)
    moderated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderation_reason: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TaskEvent(IdMixin, Base):
    """История задания с серверным временем: выдача, этапы, фото, OCR, модерация."""

    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task", "task_id", "id"),)

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ImportBatch(IdMixin, CreatedAtMixin, Base):
    """Загрузка Excel: сначала предпросмотр, потом подтверждение (ТЗ 5)."""

    __tablename__ = "import_batches"

    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="preview")
    rows: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PhotoKind(enum.StrEnum):
    STAGE = "stage"  # промежуточное фото этапа
    FINAL = "final"  # финальное фото


class OcrStatus(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"  # текст не совпал
    ERROR = "error"  # распознавание не удалось технически


class TaskPhoto(IdMixin, Base):
    """Фото хранится на диске сервера; Telegram — только дополнительная копия (ТЗ 17)."""

    __tablename__ = "task_photos"
    __table_args__ = (Index("ix_task_photos_task", "task_id", "id"),)

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    kind: Mapped[PhotoKind] = mapped_column(str_enum(PhotoKind, "photo_kind"), nullable=False)
    stage: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    # OCR (ТЗ 7.1): результат сохраняется и для неудачных попыток.
    ocr_status: Mapped[OcrStatus | None] = mapped_column(str_enum(OcrStatus, "ocr_status"))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_detail: Mapped[str | None] = mapped_column(Text)
    ocr_attempt: Mapped[int | None] = mapped_column(Integer)
