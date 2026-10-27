from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, IdMixin


class Network(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "networks"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UserNetwork(CreatedAtMixin, Base):
    """Принадлежность человека к сети. Человек может состоять в нескольких сетях."""

    __tablename__ = "user_networks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    network_id: Mapped[int] = mapped_column(
        ForeignKey("networks.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Supervision(CreatedAtMixin, Base):
    """Ребро иерархии: тренер → инструктор, инструктор → сотрудник (или тренер → сотрудник).

    У одного человека может быть несколько руководителей — команды пересекаются,
    поэтому видимость и агрегаты считаются по множеству уникальных людей.
    """

    __tablename__ = "supervisions"
    __table_args__ = (CheckConstraint("supervisor_id <> subordinate_id", name="not_self"),)

    supervisor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    subordinate_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
