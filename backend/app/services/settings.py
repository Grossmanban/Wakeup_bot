"""Системные настройки владельца (значения по умолчанию — docs/decisions.md)."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import SystemSetting
from app.services import audit
from app.services.access import Actor
from app.services.audit import Category

DEFAULTS: dict[str, Any] = {
    # График (ТЗ 9)
    "min_shifts_per_week": 1,
    # own — только свой график; busy — занятость коллег; full — подробный график коллег.
    "schedule_visibility": "own",
    # Фотоотчёты (ТЗ 17): во сколько по МСК формируется сводка за прошедший день.
    "daily_summary_time": "00:05",
    # Приглашения (ТЗ 15.5): не чаще одного раза в N часов.
    "invite_cooldown_hours": 24,
    # Реферальный бонус после подтверждённой стажировки (копейки, 0 — не начислять).
    "referral_bonus_kop": 0,
    # Предупреждение о малой выборке, если у продукта не задано своё значение.
    "min_sample_units": 20,
    # Анкета кандидата (ТЗ 12.2): навыки для самооценки 1–10 и версия согласия на обработку ПДн.
    "candidate_skills": ["Общение с людьми", "Внимательность", "Работа со смартфоном"],
    "pd_consent_version": "1.0",
    "pd_consent_url": "",
    # Шаблон сообщения кандидату от ответственного: {name}, {responsible}, {contact}.
    "candidate_contact_template": (
        "Здравствуйте, {name}! Ваша анкета одобрена. Напишите, пожалуйста, "
        "{responsible} ({contact}) в Telegram, чтобы договориться об обучении."
    ),
}

VALIDATORS = {
    "min_shifts_per_week": lambda v: isinstance(v, int) and 1 <= v <= 7,
    "schedule_visibility": lambda v: v in ("own", "busy", "full"),
    "daily_summary_time": lambda v: (
        isinstance(v, str)
        and len(v) == 5
        and v[2] == ":"
        and v[:2].isdigit()
        and v[3:].isdigit()
        and int(v[:2]) < 24
        and int(v[3:]) < 60
    ),
    "invite_cooldown_hours": lambda v: isinstance(v, int) and 0 <= v <= 24 * 30,
    "referral_bonus_kop": lambda v: isinstance(v, int) and v >= 0,
    "min_sample_units": lambda v: isinstance(v, int) and 1 <= v <= 10000,
    "candidate_skills": lambda v: (
        isinstance(v, list)
        and 0 < len(v) <= 20
        and all(isinstance(x, str) and 0 < len(x.strip()) <= 100 for x in v)
    ),
    "pd_consent_version": lambda v: isinstance(v, str) and 0 < len(v) <= 32,
    "pd_consent_url": lambda v: isinstance(v, str) and len(v) <= 500,
    "candidate_contact_template": lambda v: isinstance(v, str) and 0 < len(v) <= 2000,
}


async def get_all(session: AsyncSession) -> dict[str, Any]:
    rows = await session.execute(select(SystemSetting.key, SystemSetting.value))
    values = dict(DEFAULTS)
    values.update({k: v for k, v in rows if k in DEFAULTS})
    return values


async def get(session: AsyncSession, key: str) -> Any:
    v = await session.scalar(select(SystemSetting.value).where(SystemSetting.key == key))
    return DEFAULTS[key] if v is None else v


async def update(session: AsyncSession, actor: Actor, changes: dict[str, Any]) -> dict[str, Any]:
    actor.require_owner()
    current = await get_all(session)
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise AppError("bad_setting", f"Неизвестная настройка: {key}")
        if not VALIDATORS[key](value):
            raise AppError("bad_setting", f"Недопустимое значение настройки {key}")
    for key, value in changes.items():
        if current[key] == value:
            continue
        row = await session.get(SystemSetting, key, with_for_update=True)
        if row is None:
            session.add(SystemSetting(key=key, value=value, updated_by_id=actor.id))
        else:
            row.value = value
            row.updated_by_id = actor.id
        audit.record(
            session,
            category=Category.SETTINGS,
            action="setting.changed",
            actor_id=actor.id,
            object_type="setting",
            object_id=key,
            old=current[key],
            new=value,
        )
    await session.commit()
    return await get_all(session)
