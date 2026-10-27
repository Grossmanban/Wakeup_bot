#!/usr/bin/env sh
# Резервная копия: дамп БД (каждый день, хранится KEEP_DAYS дней) и зеркало фотографий.
# Запускать на сервере из корня проекта, например по cron:
#   15 3 * * * cd /opt/wakeup && ./deploy/backup.sh >> backups/backup.log 2>&1
# Восстановление описано в README.
set -eu
DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DIR"

docker compose exec -T db pg_dump -U wakeup -Fc wakeup > "$DIR/db-$STAMP.dump"
# Фото после загрузки не меняются: копируются только новые файлы, а не весь архив каждый день.
mkdir -p "$DIR/media"
docker compose run --rm --no-deps -v "$(cd "$DIR/media" && pwd)":/backup-media --entrypoint sh api \
  -c "cp -an /media/. /backup-media/"

find "$DIR" -name 'db-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "backup ok: $STAMP"
