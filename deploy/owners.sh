#!/usr/bin/env bash
# Владельцы Wakeup (строка OWNER_TELEGRAM_IDS в .env).
#
#   sudo bash deploy/owners.sh                  — кто владелец сейчас (с именами)
#   sudo bash deploy/owners.sh add 123456789    — добавить владельца, остальные остаются
#   sudo bash deploy/owners.sh remove 123456789 — убрать (последнего убрать нельзя)
#
# Telegram ID человека: попросите его написать @userinfobot, или в приложении
# «Управление» → «Сотрудники» → карточка человека.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
cd "$ROOT"
[ -f "$ENV_FILE" ] || { echo "Нет $ENV_FILE — сначала запустите deploy/install.sh"; exit 1; }

ids() { sed -n 's/^OWNER_TELEGRAM_IDS=//p' "$ENV_FILE" | tail -n1 | tr ',' '\n' | grep -E '^[0-9]+$' || true; }
save() { # save "id1,id2"
  if grep -q '^OWNER_TELEGRAM_IDS=' "$ENV_FILE"; then
    sed -i "s/^OWNER_TELEGRAM_IDS=.*/OWNER_TELEGRAM_IDS=$1/" "$ENV_FILE"
  else
    echo "OWNER_TELEGRAM_IDS=$1" >>"$ENV_FILE"
  fi
  echo "Перезапускаю API и бота…"
  # Пересоздаются только api и bot (база и сайт не трогаются), чтобы подхватить .env.
  docker compose up -d --no-deps --force-recreate api bot >/dev/null
}
cli() { docker compose exec -T api python -m app.cli "$@"; }

case "${1:-list}" in
  list)
    echo "Владельцы:"
    cli owners 2>/dev/null || ids
    ;;
  add)
    id="${2:-}"
    [[ "$id" =~ ^[0-9]+$ ]] || { echo "Укажите Telegram ID цифрами: owners.sh add 123456789"; exit 1; }
    if ids | grep -qx "$id"; then echo "$id уже владелец"; exit 0; fi
    save "$( (ids; echo "$id") | paste -sd, -)"
    echo "Готово: $id станет владельцем, когда откроет приложение (или уже стал, если открывал)."
    ;;
  remove)
    id="${2:-}"
    [[ "$id" =~ ^[0-9]+$ ]] || { echo "Укажите Telegram ID цифрами: owners.sh remove 123456789"; exit 1; }
    ids | grep -qx "$id" || { echo "$id нет среди владельцев"; exit 1; }
    rest="$(ids | grep -vx "$id" | paste -sd, -)"
    [ -n "$rest" ] || { echo "Нельзя убрать последнего владельца"; exit 1; }
    save "$rest"
    cli demote-owner "$id"
    ;;
  *)
    sed -n '2,9p' "$0"
    exit 1
    ;;
esac
