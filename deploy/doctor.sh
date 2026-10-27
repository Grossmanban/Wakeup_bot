#!/usr/bin/env bash
# Диагностика: почему не открывается Mini App. Вывод помещается на экран телефона.
#
#   sudo bash /opt/wakeup/deploy/doctor.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
env_get() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -n1; }
DOMAIN="$(env_get DOMAIN)"
PORT="$(env_get HTTPS_PUBLISH)"
PORT="${PORT:-443}"
MODE="$(env_get CADDY_MODE)"
# В режиме proxy снаружи сайт на 443 у Caddy соседнего приложения.
[ "$MODE" = proxy ] && PORT=443
URL="https://$DOMAIN$([ "$PORT" = 443 ] || echo ":$PORT")"
line() { printf '\n== %s\n' "$*"; }

line "Настройки"
echo "адрес $URL/ · режим ${MODE:-standard} · Mini App в .env: $(env_get MINIAPP_URL)"
echo "домен → $(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}') · IP сервера $(curl -fsS -m 8 https://api.ipify.org 2>/dev/null)"

line "Контейнеры"
docker compose ps --format '{{.Service}}: {{.State}} {{.Status}}' 2>&1

line "Порт $PORT"
ss -ltnpH "sport = :$PORT" 2>/dev/null | awk '{print $4, $6}' | head -3
command -v ufw >/dev/null && ufw status 2>/dev/null | grep -E "Status|$PORT" | head -3

line "Сертификат (с самого сервера)"
echo | timeout 10 openssl s_client -connect "127.0.0.1:$PORT" -servername "$DOMAIN" 2>/dev/null |
  openssl x509 -noout -issuer -enddate 2>/dev/null || echo "сертификата нет"

line "Ответ сайта"
printf 'изнутри: '
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' --resolve "$DOMAIN:$PORT:127.0.0.1" "$URL/api/health" 2>&1 | tail -1
printf 'по внешнему адресу: '
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' "$URL/api/health" 2>&1 | tail -1
printf 'страница: '
curl -sS -m 10 --resolve "$DOMAIN:$PORT:127.0.0.1" "$URL/" 2>/dev/null | grep -o '<script[^>]*src="[^"]*"' | head -2 | tr '\n' ' '
echo

if [ "$MODE" = proxy ]; then
  line "Caddy соседа (режим proxy)"
  N="$(docker ps --format '{{.Names}}|{{.Image}}|{{.Ports}}' | awk -F'|' '$3 ~ /:443->/ && $2 ~ /caddy/ {print $1; exit}')"
  echo "контейнер: ${N:-не найден}"
  if [ -n "$N" ]; then
    printf 'видит Wakeup: '
    docker exec "$N" wget -qO- -T 5 http://wakeup-web/api/health 2>&1 | head -c 80
    echo
    docker logs --tail=200 "$N" 2>&1 | grep -iE "$DOMAIN|wakeup" | grep -iE 'error|fail' |
      sed -E 's/.*"msg":"([^"]*)".*/\1/' | tail -4
  fi
fi

line "Ошибки web (Caddy)"
docker compose logs --no-color --tail=300 web 2>&1 | grep -iE 'error|fail|obtain|certificate|acme|duckdns|challenge' |
  sed -E 's/.*"msg":"([^"]*)".*"error":"([^"]{0,160}).*/\1: \2/; s/.*"msg":"([^"]*)".*/\1/' | tail -8

line "Ошибки api/bot"
docker compose logs --no-color --tail=200 api bot 2>&1 | grep -iE 'error|traceback|exception' | cut -c1-160 | tail -5
echo
