#!/usr/bin/env bash
# Установка и обновление Wakeup на сервере (Ubuntu/Debian).
#
#   git clone https://github.com/Grossmanban/Wakeup_bot.git /opt/wakeup
#   sudo bash /opt/wakeup/deploy/install.sh
#
# Скрипт спрашивает данные, проверяет токен бота и домен, пишет .env и запускает всё в Docker.
# Повторный запуск — обновление: Enter оставляет прежние значения, код подтягивается из git.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
DEFAULT_DOMAIN="podyomwork.duckdns.org"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die() {
  printf '\n\033[31mОшибка:\033[0m %s\n' "$*" >&2
  exit 1
}

# Значение из существующего .env (для повторного запуска).
current() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1 || true; }

ask() { # ask VAR "Вопрос" default
  local answer
  if [ -n "$3" ]; then
    read -rp "  $2 [$3]: " answer
    printf -v "$1" '%s' "${answer:-$3}"
  else
    read -rp "  $2: " answer
    printf -v "$1" '%s' "$answer"
  fi
}

[ "$(id -u)" -eq 0 ] || die "запустите через sudo: sudo bash $0"
[ -f "$ROOT/docker-compose.yml" ] || die "не найден docker-compose.yml рядом со скриптом"
cd "$ROOT"

bold "1/6 Docker"
if ! command -v docker >/dev/null 2>&1; then
  echo "  Устанавливаю Docker (несколько минут)…"
  apt-get update -qq && apt-get install -y -qq curl ca-certificates git >/dev/null
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
docker compose version >/dev/null 2>&1 || die "нет docker compose — обновите Docker"
systemctl enable --now docker >/dev/null 2>&1 || true
ok "$(docker --version)"

if [ -d .git ] && [ -f "$ENV_FILE" ]; then
  echo "  Обновляю код из git…"
  git pull --ff-only || warn "не удалось обновить код — продолжаю с текущей версией"
fi

bold "2/6 Бот"
BOT_TOKEN_OLD="$(current BOT_TOKEN)"
while :; do
  if [ -n "$BOT_TOKEN_OLD" ]; then
    read -rsp "  Токен бота от @BotFather [Enter — оставить прежний]: " BOT_TOKEN
    BOT_TOKEN="${BOT_TOKEN:-$BOT_TOKEN_OLD}"
  else
    read -rsp "  Токен бота от @BotFather (ввод скрыт): " BOT_TOKEN
  fi
  echo
  ME="$(curl -fsS --max-time 15 "https://api.telegram.org/bot$BOT_TOKEN/getMe" 2>/dev/null || true)"
  BOT_USERNAME="$(printf '%s' "$ME" | grep -o '"username":"[^"]*"' | head -n1 | cut -d'"' -f4 || true)"
  if [ -n "$BOT_USERNAME" ]; then
    ok "бот @$BOT_USERNAME"
    break
  fi
  warn "Telegram не принял токен. Скопируйте его из @BotFather целиком и попробуйте ещё раз"
  BOT_TOKEN_OLD=""
done

while :; do
  ask OWNER_TELEGRAM_IDS "Ваш Telegram ID (узнать: @userinfobot; несколько — через запятую)" \
    "$(current OWNER_TELEGRAM_IDS)"
  OWNER_TELEGRAM_IDS="${OWNER_TELEGRAM_IDS// /}"
  [[ "$OWNER_TELEGRAM_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]] && break
  warn "нужны только цифры, например 123456789"
done

bold "3/6 Домен"
CUR_DOMAIN="$(current DOMAIN)"
ask DOMAIN "Домен" "${CUR_DOMAIN:-$DEFAULT_DOMAIN}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%%/*}"
SERVER_IP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
DOMAIN_IP="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
if [ -n "$SERVER_IP" ] && [ "$SERVER_IP" = "$DOMAIN_IP" ]; then
  ok "$DOMAIN указывает на этот сервер ($SERVER_IP)"
else
  warn "$DOMAIN → ${DOMAIN_IP:-не найден}, а у сервера IP ${SERVER_IP:-не определён}"
  warn "без правильной записи HTTPS-сертификат не выпустится"
fi

DUCKDNS_TOKEN=""
if [[ "$DOMAIN" == *.duckdns.org ]]; then
  echo "  Если IP сервера может меняться, DuckDNS можно обновлять автоматически."
  ask DUCKDNS_TOKEN "Токен DuckDNS (с duckdns.org; Enter — пропустить)" "$(current DUCKDNS_TOKEN)"
fi

bold "4/6 Распознавание текста на фото (OCR)"
MEM_MB="$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)"
DEFAULT_OCR="$(current OCR_ENGINE)"
if [ -z "$DEFAULT_OCR" ]; then
  if [ "$MEM_MB" -ge 3000 ]; then DEFAULT_OCR=paddle; else DEFAULT_OCR=none; fi
fi
echo "  Памяти на сервере: ${MEM_MB} МБ. PaddleOCR нужно около 1,5 ГБ свободной памяти."
echo "  paddle — проверка текста автоматически; none — такие фото уходят на ручную проверку."
while :; do
  ask OCR_ENGINE "paddle или none" "$DEFAULT_OCR"
  [[ "$OCR_ENGINE" =~ ^(paddle|none)$ ]] && break
done
if [ "$OCR_ENGINE" = paddle ]; then WITH_OCR=1; else WITH_OCR=0; fi

POSTGRES_PASSWORD="$(current POSTGRES_PASSWORD)"
# Пароль БД задаётся один раз: смена пароля в .env не меняет его в уже созданной базе.
[ -n "$POSTGRES_PASSWORD" ] || POSTGRES_PASSWORD="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"

bold "5/6 Настройки"
[ -f "$ENV_FILE" ] && cp "$ENV_FILE" "$ENV_FILE.bak"
umask 077
cat >"$ENV_FILE" <<EOF
BOT_TOKEN=$BOT_TOKEN
BOT_USERNAME=$BOT_USERNAME
DOMAIN=$DOMAIN
MINIAPP_URL=https://$DOMAIN/
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
OWNER_TELEGRAM_IDS=$OWNER_TELEGRAM_IDS
INIT_DATA_MAX_AGE=$(current INIT_DATA_MAX_AGE | grep . || echo 86400)
CORS_ORIGINS=
OCR_ENGINE=$OCR_ENGINE
WITH_OCR=$WITH_OCR
DUCKDNS_TOKEN=$DUCKDNS_TOKEN
EOF
ok ".env сохранён (доступ только у root)"

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ok "открыты порты 80 и 443"
fi

mkdir -p "$ROOT/backups"
CRON=/etc/cron.d/wakeup
{
  echo "# Wakeup: ежедневная резервная копия БД и фото (копии старше 14 дней удаляются)"
  echo "15 3 * * * root cd $ROOT && ./deploy/backup.sh >> backups/backup.log 2>&1"
  if [ -n "$DUCKDNS_TOKEN" ]; then
    echo "# Wakeup: обновление IP в DuckDNS"
    echo "*/5 * * * * root curl -fsS 'https://www.duckdns.org/update?domains=${DOMAIN%.duckdns.org}&token=$DUCKDNS_TOKEN&ip=' >/dev/null 2>&1"
  fi
} >"$CRON"
chmod 600 "$CRON"
chmod +x "$ROOT/deploy/backup.sh"
ok "резервная копия каждый день в 03:15 → $ROOT/backups"

bold "6/6 Запуск (первая сборка — 5–15 минут)"
docker compose up -d --build --remove-orphans

echo "  Жду запуска API…"
for _ in $(seq 1 60); do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" \
    >/dev/null 2>&1; then
    ok "API работает"
    break
  fi
  sleep 3
done

# Кнопка меню бота сразу открывает Mini App.
curl -fsS --max-time 15 "https://api.telegram.org/bot$BOT_TOKEN/setChatMenuButton" \
  -H 'Content-Type: application/json' \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Открыть\",\"web_app\":{\"url\":\"https://$DOMAIN/\"}}}" \
  >/dev/null 2>&1 && ok "кнопка меню бота → https://$DOMAIN/" || warn "не удалось задать кнопку меню бота"

if curl -fsS --max-time 20 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
  ok "сайт отвечает по HTTPS: https://$DOMAIN/"
else
  warn "https://$DOMAIN/ пока не отвечает — сертификат может выпускаться ещё 1–2 минуты."
  warn "Проверка: docker compose -f $ROOT/docker-compose.yml logs web"
fi

bold "Готово"
cat <<EOF
  Откройте @$BOT_USERNAME в Telegram, нажмите /start → «Открыть приложение».
  Владелец входит без заявки и один раз указывает ФИО.

  Полезные команды (из $ROOT):
    docker compose ps                 — состояние сервисов
    docker compose logs -f api bot    — журналы
    sudo bash deploy/install.sh       — обновить код и/или изменить настройки
EOF
