#!/usr/bin/env bash
# Владельцы Wakeup (строка OWNER_TELEGRAM_IDS в .env).
#
#   sudo bash deploy/owners.sh                   — кто владелец сейчас (с именами)
#   sudo bash deploy/owners.sh people            — последние, кто открывал бота (имя, @username, ID)
#   sudo bash deploy/owners.sh add @username     — добавить владельца по username
#   sudo bash deploy/owners.sh add 123456789     — или по Telegram ID
#   sudo bash deploy/owners.sh remove 123456789  — убрать (последнего убрать нельзя)
#
# Роль владельца выдаётся сразу; человеку достаточно закрыть и снова открыть приложение.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
cd "$ROOT"
[ -f "$ENV_FILE" ] || { echo "Нет $ENV_FILE — сначала запустите deploy/install.sh"; exit 1; }

ids() { sed -n 's/^OWNER_TELEGRAM_IDS=//p' "$ENV_FILE" | tail -n1 | tr ',' '\n' | tr -d ' ' | grep -E '^[0-9]+$' || true; }
cli() { docker compose exec -T api python -m app.cli "$@"; }
restart() {
  echo "Перезапускаю API и бота…"
  # Пересоздаются только api и bot (база и сайт не трогаются), чтобы подхватить .env.
  docker compose up -d --no-deps --force-recreate api bot >/dev/null
  for _ in $(seq 1 20); do cli whois 0 >/dev/null 2>&1 && return 0; sleep 2; done
}
save() { # save "id1,id2"
  if grep -q '^OWNER_TELEGRAM_IDS=' "$ENV_FILE"; then
    sed -i "s/^OWNER_TELEGRAM_IDS=.*/OWNER_TELEGRAM_IDS=$1/" "$ENV_FILE"
  else
    echo "OWNER_TELEGRAM_IDS=$1" >>"$ENV_FILE"
  fi
  restart
}
target() { # Telegram ID из аргумента: цифры или @username
  local arg="${1:-}"
  if [[ "$arg" =~ ^[0-9]+$ ]]; then
    echo "$arg"
  elif [[ "$arg" =~ ^@?[A-Za-z0-9_]{3,}$ ]]; then
    cli resolve "$arg"
  else
    echo "Укажите @username или Telegram ID цифрами" >&2
    return 1
  fi
}

case "${1:-list}" in
  list)
    echo "Владельцы:"
    cli owners 2>/dev/null || ids
    ;;
  people)
    echo "Последние, кто открывал бота:"
    cli recent
    ;;
  add)
    id="$(target "${2:-}")" || exit 1
    echo "Это: $(cli whois "$id")"
    if ids | grep -qx "$id"; then
      echo "Уже в списке владельцев."
      # Если API запущен со старым списком — перезапустить, чтобы подхватить .env.
      docker compose exec -T api printenv OWNER_TELEGRAM_IDS 2>/dev/null | tr ',' '\n' | tr -d ' ' | grep -qx "$id" || restart
    else
      save "$( (ids; echo "$id") | paste -sd, -)"
    fi
    cli promote-owner "$id"
    echo "Готово. Пусть закроет и снова откроет приложение."
    ;;
  remove)
    id="$(target "${2:-}")" || exit 1
    ids | grep -qx "$id" || { echo "$id нет среди владельцев"; exit 1; }
    rest="$(ids | grep -vx "$id" | paste -sd, -)"
    [ -n "$rest" ] || { echo "Нельзя убрать последнего владельца"; exit 1; }
    save "$rest"
    cli demote-owner "$id"
    ;;
  *)
    sed -n '2,10p' "$0"
    exit 1
    ;;
esac
