"""Каталог разрешений (ТЗ 2.2).

Роль задаёт область видимости (иерархию), разрешения — допустимые действия.
Администратор по умолчанию не получает ничего: всё выдаёт владелец вручную.
Действия из OWNER_ONLY не выдаются никому, их выполняет только владелец.
Подробности — docs/permissions.md.
"""

from enum import StrEnum


class Perm(StrEnum):
    USERS_VIEW = "users.view"
    REGISTRATIONS_REVIEW = "registrations.review"
    NAME_CHANGES_REVIEW = "name_changes.review"
    USERS_STATUS = "users.status"
    ORG_MANAGE = "org.manage"
    AUDIT_VIEW = "audit.view"

    # Модули следующих этапов: разрешения заведены заранее, чтобы владелец
    # видел полный список, но эндпоинтов под ними пока нет.
    SCHEDULE_VIEW = "schedule.view"
    SCHEDULE_MANAGE = "schedule.manage"
    TASKS_IMPORT = "tasks.import"
    TASKS_MANAGE = "tasks.manage"
    MODERATION_REVIEW = "moderation.review"
    OCR_MANUAL_REVIEW = "ocr.manual_review"
    FINANCE_VIEW = "finance.view"
    FINANCE_MANAGE = "finance.manage"
    PAYOUTS_MANAGE = "payouts.manage"
    GOALS_MANAGE = "goals.manage"
    CANDIDATES_REVIEW = "candidates.review"
    REFERRALS_USE = "referrals.use"
    TRAINING_MANAGE = "training.manage"
    BROADCASTS_SEND = "broadcasts.send"
    GROUPS_VIEW = "groups.view"
    GROUPS_INVITE = "groups.invite"
    STATS_VIEW = "stats.view"
    EXPORT = "export"


PERM_TITLES: dict[Perm, str] = {
    Perm.USERS_VIEW: "Просмотр сотрудников",
    Perm.REGISTRATIONS_REVIEW: "Рассмотрение заявок на регистрацию",
    Perm.NAME_CHANGES_REVIEW: "Подтверждение смены ФИО",
    Perm.USERS_STATUS: "Отключение, блокировка и включение аккаунтов",
    Perm.ORG_MANAGE: "Сети и оргструктура",
    Perm.AUDIT_VIEW: "Журнал аудита",
    Perm.SCHEDULE_VIEW: "Просмотр графика коллег",
    Perm.SCHEDULE_MANAGE: "Управление графиком",
    Perm.TASKS_IMPORT: "Импорт заданий",
    Perm.TASKS_MANAGE: "Управление заданиями",
    Perm.MODERATION_REVIEW: "Модерация",
    Perm.OCR_MANUAL_REVIEW: "Ручная проверка фото",
    Perm.FINANCE_VIEW: "Просмотр финансов",
    Perm.FINANCE_MANAGE: "Штрафы, бонусы, корректировки",
    Perm.PAYOUTS_MANAGE: "Выплаты",
    Perm.GOALS_MANAGE: "Цели и соревнования",
    Perm.CANDIDATES_REVIEW: "Кандидаты",
    Perm.REFERRALS_USE: "Реферальная ссылка",
    Perm.TRAINING_MANAGE: "Обучение",
    Perm.BROADCASTS_SEND: "Рассылки",
    Perm.GROUPS_VIEW: "Членство в группах",
    Perm.GROUPS_INVITE: "Приглашения в группы",
    Perm.STATS_VIEW: "Статистика",
    Perm.EXPORT: "Экспорт",
}

# Только владелец: роли, выдача разрешений, продуктовые доступы, продукты и ставки,
# настройки групп и фотоотчётов. Не выдаются как разрешения.
OWNER_ONLY = (
    "roles.assign",
    "permissions.grant",
    "products.manage",
    "products.access",
    "rates.manage",
    "groups.manage",
    "photoreports.manage",
    "settings.manage",
)
