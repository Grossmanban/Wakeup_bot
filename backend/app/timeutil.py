"""Время бизнес-логики — Europe/Moscow. В БД всё хранится в timestamptz (UTC)."""

from datetime import UTC, date, datetime, time, timedelta
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


def msk_today() -> date:
    return msk_date(now_utc())


def msk_at(day: date, t: time = time(0, 0)) -> datetime:
    """Момент времени: день и время по МСК."""
    return datetime.combine(day, t, tzinfo=MSK)


def msk_day_bounds(start: date, end_inclusive: date) -> tuple[datetime, datetime]:
    """Полуинтервал [начало start, начало end+1) по МСК для фильтрации timestamptz."""
    return msk_at(start), msk_at(end_inclusive + timedelta(days=1))


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())
