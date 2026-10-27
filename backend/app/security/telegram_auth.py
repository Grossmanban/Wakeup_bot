"""Проверка Telegram Mini App initData на сервере.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataError(Exception):
    pass


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str | None
    last_name: str | None
    username: str | None


@dataclass(frozen=True)
class InitData:
    user: TelegramUser
    auth_date: int
    start_param: str | None


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Подписать поля так же, как это делает Telegram. Используется в тестах."""
    from urllib.parse import urlencode

    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    digest = hmac.new(_secret_key(bot_token), dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def validate_init_data(
    raw: str, bot_token: str, max_age: int, now: float | None = None
) -> InitData:
    if not raw:
        raise InitDataError("пустые initData")
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=False)
    fields: dict[str, str] = {}
    for k, v in pairs:
        if k in fields:
            raise InitDataError("повторяющееся поле")
        fields[k] = v

    received = fields.pop("hash", None)
    if not received:
        raise InitDataError("нет подписи")

    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    expected = hmac.new(_secret_key(bot_token), dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise InitDataError("неверная подпись")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError) as e:
        raise InitDataError("нет auth_date") from e
    now = time.time() if now is None else now
    if max_age > 0 and now - auth_date > max_age:
        raise InitDataError("initData устарели")

    try:
        u = json.loads(fields["user"])
        user = TelegramUser(
            id=int(u["id"]),
            first_name=u.get("first_name"),
            last_name=u.get("last_name"),
            username=u.get("username"),
        )
    except (KeyError, ValueError, TypeError) as e:
        raise InitDataError("нет данных пользователя") from e

    return InitData(user=user, auth_date=auth_date, start_param=fields.get("start_param"))
