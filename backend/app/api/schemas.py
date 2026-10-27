from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import AccountStatus, RequestStatus, Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserShort(ORM):
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    full_name: str
    role: Role
    status: AccountStatus


class UserOut(UserShort):
    tg_first_name: str | None
    tg_last_name: str | None
    status_reason: str | None
    app_access: bool
    can_message: bool
    created_at: datetime


class RegistrationIn(BaseModel):
    first_name: str = Field(max_length=200)
    last_name: str = Field(max_length=200)


class RegistrationOut(ORM):
    id: int
    user_id: int
    first_name: str
    last_name: str
    status: RequestStatus
    reason: str | None
    created_at: datetime
    decided_at: datetime | None
    decided_by_id: int | None


class RegistrationWithUser(RegistrationOut):
    user: UserShort


class NameChangeOut(ORM):
    id: int
    user_id: int
    old_first_name: str | None
    old_last_name: str | None
    new_first_name: str
    new_last_name: str
    status: RequestStatus
    reason: str | None
    created_at: datetime
    decided_at: datetime | None


class NameChangeWithUser(NameChangeOut):
    user: UserShort


class DecisionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class MeOut(BaseModel):
    user: UserOut
    # Что показать на первом экране: register | candidate | pending | rejected | disabled
    # | blocked | no_app_access | ready
    screen: str
    message: str | None
    registration: RegistrationOut | None
    name_change: NameChangeOut | None
    is_owner: bool
    permissions: list[str]
    has_product_access: bool
    product_access_message: str | None
    training_status: str = "none"


class RoleIn(BaseModel):
    role: Role


class StatusIn(BaseModel):
    status: AccountStatus
    reason: str | None = Field(default=None, max_length=1000)


class AppAccessIn(BaseModel):
    enabled: bool


class Grant(BaseModel):
    permission: str
    network_id: int | None = None


class PermissionsIn(BaseModel):
    grants: list[Grant]


class PermissionsOut(BaseModel):
    grants: list[Grant]


class CatalogItem(BaseModel):
    code: str
    title: str


class NetworkIn(BaseModel):
    name: str = Field(max_length=128)


class NetworkPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    is_archived: bool | None = None


class NetworkOut(ORM):
    id: int
    name: str
    is_archived: bool


class UserNetworksIn(BaseModel):
    network_ids: list[int]


class SupervisionIn(BaseModel):
    supervisor_id: int
    subordinate_id: int


class UserProfileOut(BaseModel):
    user: UserOut
    networks: list[NetworkOut]
    supervisors: list[UserShort]
    subordinates: list[UserShort]
    grants: list[Grant] | None  # видны только владельцу
    pending_name_change: NameChangeOut | None


class AuditOut(ORM):
    id: int
    occurred_at: datetime
    category: str
    action: str
    actor_id: int | None
    target_user_id: int | None
    object_type: str | None
    object_id: str | None
    old_value: Any
    new_value: Any
    reason: str | None
    result: str


class Page[T](BaseModel):
    items: list[T]
    total: int
