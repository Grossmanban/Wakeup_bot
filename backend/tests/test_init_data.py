import json
import time
from urllib.parse import parse_qsl, urlencode

import pytest

from app.security.telegram_auth import InitDataError, validate_init_data
from tests.helpers import BOT_TOKEN, auth, init_data


def test_valid():
    data = validate_init_data(init_data(42, username="ivan"), BOT_TOKEN, 3600)
    assert data.user.id == 42
    assert data.user.username == "ivan"


def test_wrong_token():
    with pytest.raises(InitDataError):
        validate_init_data(init_data(42), "999:OTHER", 3600)


def test_tampered_user_id():
    fields = dict(parse_qsl(init_data(42)))
    user = json.loads(fields["user"])
    user["id"] = 1000
    fields["user"] = json.dumps(user)
    with pytest.raises(InitDataError, match="подпись"):
        validate_init_data(urlencode(fields), BOT_TOKEN, 3600)


def test_expired():
    old = init_data(42, auth_date=time.time() - 7200)
    with pytest.raises(InitDataError, match="устарели"):
        validate_init_data(old, BOT_TOKEN, 3600)


def test_missing_hash():
    fields = dict(parse_qsl(init_data(42)))
    fields.pop("hash")
    with pytest.raises(InitDataError):
        validate_init_data(urlencode(fields), BOT_TOKEN, 3600)


def test_duplicate_field_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(init_data(42) + "&user=%7B%22id%22%3A1%7D", BOT_TOKEN, 3600)


async def test_api_requires_signed_init_data(client):
    assert (await client.get("/api/me")).status_code == 401
    r = await client.get("/api/me", headers={"Authorization": "tma user=%7B%22id%22%3A1000%7D"})
    assert r.status_code == 401
    assert (await client.get("/api/me", headers=auth(5))).status_code == 200
