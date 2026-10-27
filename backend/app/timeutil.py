"""Время бизнес-логики — Europe/Moscow. В БД всё хранится в timestamptz (UTC)."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_msk(dt: datetime) -> datetime:
    return dt.astimezone(MSK)


def msk_date(dt: datetime) -> date:
    """Календарная дата события по МСК."""
    return to_msk(dt).date()


def format_msk(dt: datetime) -> str:
    return to_msk(dt).strftime("%d.%m.%Y, %H:%M МСК")
