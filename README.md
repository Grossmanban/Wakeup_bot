# Wakeup

Telegram-бот и Mini App для управления сотрудниками, заданиями, графиком, финансами,
аналитикой и фотоотчётами.

- ТЗ: [docs/tz.md](docs/tz.md)
- План и статус этапов: [docs/plan.md](docs/plan.md)
- Решения, значения по умолчанию, ограничения Telegram: [docs/decisions.md](docs/decisions.md)
- Роли и разрешения: [docs/permissions.md](docs/permissions.md)
- Модель данных: [docs/data-model.md](docs/data-model.md)

## Состав

```
backend/   API (FastAPI), Telegram-бот и обработчик очереди (aiogram), миграции, тесты
webapp/    Mini App (React + TypeScript + Vite)
deploy/    Caddy, сборка Mini App, резервное копирование
```

## Запуск на сервере

Нужны Docker с Compose и домен, A-запись которого указывает на сервер (Telegram открывает
Mini App только по HTTPS).

1. Создайте бота у @BotFather и получите токен.
2. Скопируйте `.env.example` в `.env` и заполните: `BOT_TOKEN`, `DOMAIN`, `MINIAPP_URL`,
   `POSTGRES_PASSWORD`, `OWNER_TELEGRAM_IDS`.
3. `docker compose up -d --build`
4. В @BotFather (Bot Settings → Configure Mini App) укажите адрес `https://<DOMAIN>/`.
5. Откройте бота, нажмите /start → «Открыть приложение». Владелец входит без заявки и
   один раз указывает ФИО.

Миграции применяются автоматически при старте контейнера `api`. HTTPS-сертификат Caddy
получает сам.

### Резервные копии

`deploy/backup.sh` сохраняет дамп БД и архив фотографий в `./backups` и удаляет копии старше
14 дней. Пример строки для cron — в начале скрипта.

Восстановление:

```sh
docker compose up -d db
docker compose exec -T db pg_restore -U wakeup -d wakeup --clean --if-exists < backups/db-<дата>.dump
docker compose run --rm --no-deps -v "$PWD/backups":/backup --entrypoint sh api \
  -c "tar -xzf /backup/media-<дата>.tar.gz -C /media"
docker compose up -d
```

## Разработка

Backend (нужен PostgreSQL и [uv](https://docs.astral.sh/uv/)):

```sh
cd backend
uv sync
export BOT_TOKEN=... DATABASE_URL=postgresql+asyncpg://wakeup:wakeup@localhost:5432/wakeup OWNER_TELEGRAM_IDS=...
uv run alembic upgrade head
uv run uvicorn app.main:app --reload     # API на :8000
uv run python -m bot.main                # бот и очередь сообщений
```

Тесты используют отдельную БД `wakeup_test` (или `TEST_DATABASE_URL`) и пересоздают схему
миграциями:

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Mini App:

```sh
cd webapp
npm install
npm run dev      # :5173, /api проксируется на :8000
npm run build
```

Вне Telegram Mini App показывает «Откройте приложение из Telegram»: вход возможен только
с подписанными initData.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `BOT_TOKEN` | Токен бота. Не пишется в логи |
| `DATABASE_URL` | Подключение к PostgreSQL (в Compose задаётся автоматически) |
| `POSTGRES_PASSWORD` | Пароль БД для Compose |
| `DOMAIN` | Домен для HTTPS |
| `MINIAPP_URL` | Адрес Mini App для кнопок бота |
| `OWNER_TELEGRAM_IDS` | Telegram ID владельцев через запятую |
| `INIT_DATA_MAX_AGE` | Срок действия подписи входа, секунд (по умолчанию 86400) |
| `CORS_ORIGINS` | Только при раздельных доменах Mini App и API |
| `MEDIA_DIR` | Каталог фотографий (в Compose — том `media`) |
