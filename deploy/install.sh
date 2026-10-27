#!/usr/bin/env bash
# Установка и обновление Wakeup на сервере (Ubuntu/Debian).
#
#   git clone https://github.com/Grossmanban/Wakeup_bot.git /opt/wakeup
#   sudo bash /opt/wakeup/deploy/install.sh
#
# Скрипт спрашивает данные, проверяет токен бота и домен, пишет .env и запускает всё в Docker.
# Повторный запуск — обновление: Enter оставляет прежние значения, код подтягивается из git.
#
# Уживается с другими приложениями на сервере: свой проект Docker (wakeup-*), Docker не
# переустанавливается и не перезапускается. Если порты 80/443 уже заняты (например, другим
# ботом со своим Caddy), Wakeup открывается на отдельном порту (по умолчанию 8443), а
# сертификат получает через DNS-проверку DuckDNS — чужие сервисы не трогаются.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
DEFAULT_DOMAIN="podyomwork.duckdns.org"
PROJECT=wakeup

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

# Кто слушает порт, кроме самого Wakeup. Пусто — порт свободен (или занят нами).
port_owner() {
  local p="$1"
  if docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Ports}}' \
    2>/dev/null | grep -q ":$p->"; then
    return 0
  fi
  local who
  who="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep ":$p->" | awk '{print $1}' | paste -sd, - || true)"
  if [ -z "$who" ] && command -v ss >/dev/null 2>&1; then
    who="$(ss -ltnpH "sport = :$p" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -n1 | cut -d'"' -f2 || true)"
    [ -n "$who" ] || { ss -ltnH "sport = :$p" 2>/dev/null | grep -q . && who="другой процесс"; } || true
  fi
  printf '%s' "$who"
}

# Другие приложения в /opt с тем же значением в .env (токен бота, домен).
others_with() { # others_with KEY VALUE
  local f
  for f in /opt/*/.env; do
    [ -f "$f" ] && [ "$(cd "$(dirname "$f")" && pwd)" != "$ROOT" ] || continue
    grep -Fqx "$1=$2" "$f" 2>/dev/null && dirname "$f"
  done
  return 0
}

[ "$(id -u)" -eq 0 ] || die "запустите через sudo: sudo bash $0"
[ -f "$ROOT/docker-compose.yml" ] || die "не найден docker-compose.yml рядом со скриптом"
cd "$ROOT"

bold "1/7 Сервер"
curl -fsS -m 15 -o /dev/null https://api.telegram.org ||
  die "сервер не достаёт до api.telegram.org — бот здесь работать не сможет"
ok "Telegram API доступен"

MEM_MB="$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)"
AVAIL_MB="$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)"
if [ "$(swapon --show --noheadings 2>/dev/null | wc -l)" -eq 0 ] && [ "$MEM_MB" -lt 3000 ] &&
  [ ! -e /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  ok "включён swap 2 ГБ (памяти мало для сборки)"
fi
DISK_GB="$(df -BG --output=avail / | tail -n1 | tr -dc '0-9')"
ok "память ${MEM_MB} МБ (свободно ${AVAIL_MB} МБ), диск свободно ${DISK_GB} ГБ"
[ "$DISK_GB" -ge 10 ] || warn "мало места на диске: нужно хотя бы 10 ГБ под образы и фото"

if ! command -v docker >/dev/null 2>&1; then
  echo "  Устанавливаю Docker (несколько минут)…"
  apt-get update -qq && apt-get install -y -qq curl ca-certificates git >/dev/null
  curl -fsSL https://get.docker.com | sh >/dev/null
  # Docker Hub бывает недоступен из российских сетей: зеркало Google как запасной источник.
  if [ ! -f /etc/docker/daemon.json ]; then
    mkdir -p /etc/docker
    echo '{ "registry-mirrors": ["https://mirror.gcr.io"] }' >/etc/docker/daemon.json
    systemctl restart docker
  fi
fi
docker compose version >/dev/null 2>&1 || die "нет docker compose — обновите Docker"
systemctl enable --now docker >/dev/null 2>&1 || true
ok "$(docker --version)"

if [ -d .git ] && [ -f "$ENV_FILE" ]; then
  echo "  Обновляю код из git…"
  git pull --ff-only || warn "не удалось обновить код — продолжаю с текущей версией"
fi

bold "2/7 Бот"
BOT_TOKEN_OLD="$(current BOT_TOKEN)"
while :; do
  if [ -n "$BOT_TOKEN_OLD" ]; then
    read -rsp "  Токен бота от @BotFather [Enter — оставить прежний]: " BOT_TOKEN
    BOT_TOKEN="${BOT_TOKEN:-$BOT_TOKEN_OLD}"
  else
    read -rsp "  Токен бота от @BotFather (ввод скрыт): " BOT_TOKEN
  fi
  echo
  BOT_TOKEN="${BOT_TOKEN// /}"
  ME="$(curl -fsS --max-time 15 "https://api.telegram.org/bot$BOT_TOKEN/getMe" 2>/dev/null || true)"
  BOT_USERNAME="$(printf '%s' "$ME" | grep -o '"username":"[^"]*"' | head -n1 | cut -d'"' -f4 || true)"
  if [ -z "$BOT_USERNAME" ]; then
    warn "Telegram не принял токен. Скопируйте его из @BotFather целиком и попробуйте ещё раз"
    BOT_TOKEN_OLD=""
    continue
  fi
  CLASH="$(others_with BOT_TOKEN "$BOT_TOKEN")"
  if [ -n "$CLASH" ]; then
    # Два процесса не могут одновременно получать обновления одного бота.
    warn "этот токен уже использует приложение в $CLASH — нужен отдельный бот для Wakeup"
    warn "создайте его в @BotFather (/newbot) и введите его токен"
    BOT_TOKEN_OLD=""
    continue
  fi
  ok "бот @$BOT_USERNAME"
  break
done

while :; do
  ask OWNER_TELEGRAM_IDS "Ваш Telegram ID (узнать: @userinfobot; несколько — через запятую)" \
    "$(current OWNER_TELEGRAM_IDS)"
  OWNER_TELEGRAM_IDS="${OWNER_TELEGRAM_IDS// /}"
  [[ "$OWNER_TELEGRAM_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]] && break
  warn "нужны только цифры, например 123456789"
done

bold "3/7 Домен"
while :; do
  CUR_DOMAIN="$(current DOMAIN)"
  ask DOMAIN "Домен" "${CUR_DOMAIN:-$DEFAULT_DOMAIN}"
  DOMAIN="${DOMAIN#https://}"
  DOMAIN="${DOMAIN%%/*}"
  DOMAIN="${DOMAIN%%:*}"
  CLASH="$(others_with DOMAIN "$DOMAIN")"
  [ -z "$CLASH" ] && break
  warn "домен $DOMAIN уже занят приложением в $CLASH — укажите другой (например, ещё один на duckdns.org)"
done
SERVER_IP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
DOMAIN_IP="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
if [ -n "$SERVER_IP" ] && [ "$SERVER_IP" = "$DOMAIN_IP" ]; then
  ok "$DOMAIN указывает на этот сервер ($SERVER_IP)"
else
  warn "$DOMAIN → ${DOMAIN_IP:-не найден}, а у сервера IP ${SERVER_IP:-не определён}"
  warn "без правильной записи HTTPS-сертификат не выпустится"
fi

bold "4/7 Порты"
BUSY80="$(port_owner 80)"
BUSY443="$(port_owner 443)"
if [ -z "$BUSY80" ] && [ -z "$BUSY443" ]; then
  CADDY_MODE=standard
  HTTP_PUBLISH=80
  HTTPS_PUBLISH=443
  BASE_URL="https://$DOMAIN"
  ok "порты 80 и 443 свободны — Wakeup откроется на https://$DOMAIN/"
else
  CADDY_MODE=duckdns
  warn "порты 80/443 заняты: ${BUSY80:-—} / ${BUSY443:-—}. Их не трогаю."
  [[ "$DOMAIN" == *.duckdns.org ]] ||
    die "при занятых 80/443 сертификат выпускается через DuckDNS — нужен домен *.duckdns.org"
  CUR_PORT="$(current HTTPS_PUBLISH)"
  [ -n "$CUR_PORT" ] && [ "$CUR_PORT" != 443 ] || CUR_PORT=8443
  while :; do
    ask HTTPS_PUBLISH "Порт для Wakeup" "$CUR_PORT"
    if ! [[ "$HTTPS_PUBLISH" =~ ^[0-9]+$ ]] || [ "$HTTPS_PUBLISH" -lt 1024 ] || [ "$HTTPS_PUBLISH" -gt 65535 ]; then
      warn "нужен номер порта от 1024 до 65535"
      continue
    fi
    BUSY="$(port_owner "$HTTPS_PUBLISH")"
    [ -z "$BUSY" ] && break
    warn "порт $HTTPS_PUBLISH занят ($BUSY), выберите другой"
  done
  # Порт 80 контейнера публикуется только на localhost: наружу он не нужен.
  HTTP_PUBLISH="127.0.0.1:18080"
  BASE_URL="https://$DOMAIN:$HTTPS_PUBLISH"
  ok "Wakeup откроется на $BASE_URL/"
fi

DUCKDNS_TOKEN="$(current DUCKDNS_TOKEN)"
if [[ "$DOMAIN" == *.duckdns.org ]]; then
  if [ "$CADDY_MODE" = duckdns ]; then
    echo "  Токен DuckDNS нужен для сертификата (duckdns.org → вверху страницы, поле token)."
  else
    echo "  Если IP сервера может меняться, DuckDNS можно обновлять автоматически."
  fi
  while :; do
    ask DUCKDNS_TOKEN "Токен DuckDNS$([ "$CADDY_MODE" = duckdns ] || echo ' (Enter — пропустить)')" \
      "$DUCKDNS_TOKEN"
    DUCKDNS_TOKEN="${DUCKDNS_TOKEN// /}"
    if [ -z "$DUCKDNS_TOKEN" ]; then
      [ "$CADDY_MODE" = duckdns ] && { warn "без токена сертификат не выпустится"; continue; }
      break
    fi
    # Проверка токена: DuckDNS отвечает OK, если токен подходит к домену.
    R="$(curl -fsS --max-time 15 "https://www.duckdns.org/update?domains=${DOMAIN%.duckdns.org}&token=$DUCKDNS_TOKEN&ip=${SERVER_IP:-}" 2>/dev/null || true)"
    [ "$R" = OK ] && { ok "токен DuckDNS подходит"; break; }
    warn "DuckDNS не принял токен для ${DOMAIN%.duckdns.org} (ответ: ${R:-нет ответа})"
  done
else
  DUCKDNS_TOKEN=""
fi

bold "5/7 Распознавание текста на фото (OCR)"
DEFAULT_OCR="$(current OCR_ENGINE)"
if [ -z "$DEFAULT_OCR" ]; then
  if [ "$AVAIL_MB" -ge 2500 ]; then DEFAULT_OCR=paddle; else DEFAULT_OCR=none; fi
fi
echo "  Свободно памяти: ${AVAIL_MB} МБ (с учётом уже работающих программ). PaddleOCR нужно ~1,5 ГБ."
echo "  paddle — проверка текста автоматически; none — такие фото уходят на ручную проверку."
while :; do
  ask OCR_ENGINE "paddle или none" "$DEFAULT_OCR"
  [[ "$OCR_ENGINE" =~ ^(paddle|none)$ ]] && break
done
if [ "$OCR_ENGINE" = paddle ]; then WITH_OCR=1; else WITH_OCR=0; fi

POSTGRES_PASSWORD="$(current POSTGRES_PASSWORD)"
# Пароль БД задаётся один раз: смена пароля в .env не меняет его в уже созданной базе.
[ -n "$POSTGRES_PASSWORD" ] || POSTGRES_PASSWORD="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
INIT_DATA_MAX_AGE="$(current INIT_DATA_MAX_AGE)"

bold "6/7 Настройки"
[ -f "$ENV_FILE" ] && cp "$ENV_FILE" "$ENV_FILE.bak"
umask 077
cat >"$ENV_FILE" <<EOF
BOT_TOKEN=$BOT_TOKEN
BOT_USERNAME=$BOT_USERNAME
DOMAIN=$DOMAIN
MINIAPP_URL=$BASE_URL/
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
OWNER_TELEGRAM_IDS=$OWNER_TELEGRAM_IDS
INIT_DATA_MAX_AGE=${INIT_DATA_MAX_AGE:-86400}
CORS_ORIGINS=
OCR_ENGINE=$OCR_ENGINE
WITH_OCR=$WITH_OCR
DUCKDNS_TOKEN=$DUCKDNS_TOKEN
CADDY_MODE=$CADDY_MODE
HTTP_PUBLISH=$HTTP_PUBLISH
HTTPS_PUBLISH=$HTTPS_PUBLISH
EOF
ok ".env сохранён (доступ только у root)"

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  if [ "$CADDY_MODE" = standard ]; then
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ok "в ufw открыты порты 80 и 443"
  else
    ufw allow "$HTTPS_PUBLISH/tcp" >/dev/null && ok "в ufw открыт порт $HTTPS_PUBLISH"
  fi
fi

mkdir -p "$ROOT/backups"
CRON=/etc/cron.d/wakeup
{
  echo "# Wakeup: ежедневная резервная копия БД и фото"
  echo "15 3 * * * root cd $ROOT && ./deploy/backup.sh >> backups/backup.log 2>&1"
  if [ -n "$DUCKDNS_TOKEN" ]; then
    echo "# Wakeup: обновление IP в DuckDNS"
    echo "*/5 * * * * root curl -fsS 'https://www.duckdns.org/update?domains=${DOMAIN%.duckdns.org}&token=$DUCKDNS_TOKEN&ip=' >/dev/null 2>&1"
  fi
} >"$CRON"
chmod 600 "$CRON"
chmod +x "$ROOT/deploy/backup.sh"
ok "резервная копия каждый день в 03:15 → $ROOT/backups"

bold "7/7 Запуск (первая сборка — 5–15 минут)"
# Затрагивает только проект wakeup: контейнеры других приложений не перезапускаются.
docker compose -p "$PROJECT" up -d --build --remove-orphans

echo "  Жду запуска API…"
API_OK=""
for _ in $(seq 1 60); do
  if docker compose -p "$PROJECT" exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" \
    >/dev/null 2>&1; then
    API_OK=1
    ok "API работает"
    break
  fi
  sleep 3
done
[ -n "$API_OK" ] || warn "API не ответил за 3 минуты. Журнал: docker compose -p $PROJECT logs --tail=50 api"

# Кнопка меню бота сразу открывает Mini App.
if curl -fsS --max-time 15 "https://api.telegram.org/bot$BOT_TOKEN/setChatMenuButton" \
  -H 'Content-Type: application/json' \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Открыть\",\"web_app\":{\"url\":\"$BASE_URL/\"}}}" \
  >/dev/null 2>&1; then
  ok "кнопка меню бота → $BASE_URL/"
else
  warn "не удалось задать кнопку меню бота"
fi

echo "  Жду HTTPS-сертификат…"
WEB_OK=""
for _ in $(seq 1 30); do
  if curl -fsS --max-time 10 "$BASE_URL/api/health" >/dev/null 2>&1; then
    WEB_OK=1
    ok "сайт отвечает по HTTPS: $BASE_URL/"
    break
  fi
  sleep 6
done
if [ -z "$WEB_OK" ]; then
  warn "$BASE_URL/ пока не отвечает. Сертификат через DuckDNS может выпускаться до 5 минут."
  warn "Журнал: docker compose -p $PROJECT logs --tail=50 web"
fi

bold "Готово"
cat <<EOF
  Mini App: $BASE_URL/
  В @BotFather → Bot Settings → Configure Mini App укажите этот адрес.
  Откройте @$BOT_USERNAME в Telegram, нажмите /start → «Открыть приложение».
  Владелец входит без заявки и один раз указывает ФИО.

  Полезные команды (из $ROOT):
    docker compose ps                 — состояние сервисов Wakeup
    docker compose logs -f api bot    — журналы
    sudo bash deploy/install.sh       — обновить код и/или изменить настройки
EOF
