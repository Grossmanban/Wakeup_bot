from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.models import OcrRule, ProductStatus, TaskStatus

RequiredField = Literal[
    "link", "expected_text", "rating", "instructions", "comment", "materials", "gender"
]


class Link(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    url: HttpUrl


class FlowStage(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    instructions: str | None = Field(default=None, max_length=2000)
    checklist: list[str] = Field(default_factory=list, max_length=20)
    # Кнопка «Далее» доступна не раньше, чем через столько секунд после начала этапа.
    min_seconds: int = Field(default=0, ge=0, le=24 * 3600)
    photo: Literal["none", "optional", "required"] = "none"

    @field_validator("checklist")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        v = [" ".join(x.split()) for x in v]
        if any(not x or len(x) > 200 for x in v):
            raise ValueError("Пункт чек-листа: от 1 до 200 символов")
        return v


class ProductSettings(BaseModel):
    description: str | None = Field(default=None, max_length=5000)
    active_task_limit: int = Field(default=1, ge=1, le=100)
    instructions: str | None = Field(default=None, max_length=10000)
    links: list[Link] = Field(default_factory=list, max_length=30)
    materials: list[Link] = Field(default_factory=list, max_length=30)
    required_fields: list[RequiredField] = Field(default_factory=list)
    gender: Literal["m", "f"] | None = None
    expected_text_default: str | None = Field(default=None, max_length=2000)
    flow: list[FlowStage] = Field(default_factory=list, max_length=20)
    final_photo_required: bool = False
    timer_reset_reason_required: bool = True
    error_reasons: list[str] = Field(default_factory=list, max_length=30)
    allow_return_for_fix: bool = True
    ocr_enabled: bool = False
    ocr_rule: OcrRule = OcrRule.CONTAINS
    ocr_threshold: int = Field(default=85, ge=50, le=100)
    ocr_attempts: int = Field(default=3, ge=1, le=10)
    training_volume: int = Field(default=0, ge=0, le=100000)
    min_sample_units: int = Field(default=20, ge=1, le=10000)

    @field_validator("error_reasons")
    @classmethod
    def _reasons(cls, v: list[str]) -> list[str]:
        v = [" ".join(x.split()) for x in v]
        if any(not x or len(x) > 200 for x in v):
            raise ValueError("Причина ошибки: от 1 до 200 символов")
        return v


class ProductIn(ProductSettings):
    name: str = Field(min_length=1, max_length=128)


class ProductPatch(BaseModel):
    """Частичное изменение: передаются только изменяемые поля."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=5000)
    active_task_limit: int | None = Field(default=None, ge=1, le=100)
    instructions: str | None = Field(default=None, max_length=10000)
    links: list[Link] | None = Field(default=None, max_length=30)
    materials: list[Link] | None = Field(default=None, max_length=30)
    required_fields: list[RequiredField] | None = None
    gender: Literal["m", "f"] | None = None
    expected_text_default: str | None = Field(default=None, max_length=2000)
    flow: list[FlowStage] | None = Field(default=None, max_length=20)
    final_photo_required: bool | None = None
    timer_reset_reason_required: bool | None = None
    error_reasons: list[str] | None = Field(default=None, max_length=30)
    allow_return_for_fix: bool | None = None
    ocr_enabled: bool | None = None
    ocr_rule: OcrRule | None = None
    ocr_threshold: int | None = Field(default=None, ge=50, le=100)
    ocr_attempts: int | None = Field(default=None, ge=1, le=10)
    training_volume: int | None = Field(default=None, ge=0, le=100000)
    min_sample_units: int | None = Field(default=None, ge=1, le=10000)


def dump_settings(model: BaseModel, *, partial: bool) -> dict[str, Any]:
    data = model.model_dump(exclude_unset=partial, mode="json")
    if partial:
        # gender и тексты можно явно сбросить в null; остальные null означают «не менять».
        nullable = {"gender", "description", "instructions", "expected_text_default"}
        data = {k: v for k, v in data.items() if v is not None or k in nullable}
    return data


class ProductOut(ProductSettings):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: ProductStatus
    links: list[dict[str, Any]]
    materials: list[dict[str, Any]]
    flow: list[dict[str, Any]]
    created_at: datetime


class ProductListItem(BaseModel):
    id: int
    name: str
    status: ProductStatus
    pool: int
    active: int
    on_moderation: int


class StatusIn(BaseModel):
    status: ProductStatus


class TaskTypeIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=128)
    excel_names: list[str] = Field(default_factory=list, max_length=30)
    units: int = Field(default=1, ge=1, le=1000)
    is_active: bool = True


class TaskTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    excel_names: list[str]
    units: int
    is_active: bool


class RateIn(BaseModel):
    # Копейки; null — снять ставку.
    amount_kop: int | None = Field(default=None, ge=0, le=10**10)


class RatesOut(BaseModel):
    roles: dict[str, int]
    users: list[dict[str, Any]]
    history: list[dict[str, Any]]


class IdsIn(BaseModel):
    ids: list[int] = Field(max_length=1000)


class GenderIn(BaseModel):
    gender: Literal["m", "f"] | None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    task_type_id: int | None
    external_id: str
    link: str | None
    expected_text: str | None
    rating: str | None
    instructions: str | None
    comment: str | None
    materials: list[Any]
    gender: str | None
    extra: dict[str, Any]
    units: int
    status: TaskStatus
    assignee_id: int | None
    assigned_at: datetime | None
    opened_at: datetime | None
    found_at: datetime | None
    current_stage: int
    stage_started_at: datetime | None
    completed_at: datetime | None
    ocr_attempts_used: int
    manual_review_reason: str | None
    moderated_at: datetime | None
    moderation_reason: str | None
    created_at: datetime


class TaskListItem(TaskOut):
    product_name: str
    assignee_name: str | None


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class ImportPreviewOut(BaseModel):
    id: int
    file_name: str
    status: str
    summary: dict[str, Any]
    rows: list[dict[str, Any]]
    result: dict[str, Any] | None


class MyProduct(BaseModel):
    id: int
    name: str
    description: str | None
    instructions: str | None
    links: list[dict[str, Any]]
    materials: list[dict[str, Any]]
    status: ProductStatus
    pool: int
    active: int
    active_task_limit: int
    rate_kop: int | None
    rate_error: str | None


class UserProductsOut(BaseModel):
    product_ids: list[int]
    gender: str | None
    rates: list[dict[str, Any]]
