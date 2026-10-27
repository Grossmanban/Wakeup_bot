"""Тестовые таблицы импорта (docs/samples) дают ровно те результаты, что описаны в них."""

import asyncio
import importlib.util
from pathlib import Path

from tests.helpers import OWNER_TG, auth, owner_ready

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_import_sample.py"
spec = importlib.util.spec_from_file_location("make_import_sample", SCRIPT)
sample = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sample)


async def _setup_products(client) -> dict[str, int]:
    h = auth(OWNER_TG)
    ids = {}
    for p in sample.PRODUCTS:
        r = await client.post(
            "/api/products",
            json={
                "name": p["name"],
                "required_fields": p["required_fields"],
                "expected_text_default": p["expected_text_default"],
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        ids[p["name"]] = r.json()["id"]
        if p["types"]:
            r = await client.put(
                f"/api/products/{ids[p['name']]}/task-types", json=p["types"], headers=h
            )
            assert r.status_code == 200, r.text
    return ids


async def _upload(client, path: Path) -> dict:
    content = await asyncio.to_thread(path.read_bytes)
    r = await client.post(
        "/api/imports",
        files={"file": (path.name, content)},
        headers=auth(OWNER_TG),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _check(batch: dict, rows) -> None:
    got = {r["row"]: r for r in batch["rows"]}
    for n, (_, status, what, errors) in enumerate(rows, start=2):
        r = got[n]
        assert r["status"] == status, (n, what, r)
        for e in errors:
            assert any(e in x for x in r["errors"]), (n, what, r["errors"])
    s = batch["summary"]
    assert (s["ok"], s["errors"], s["duplicates"], s["skipped"]) == tuple(
        sum(1 for r in rows if r[1] == st) for st in ("ok", "error", "duplicate", "skipped")
    )


async def test_sample_files(client, tmp_path):
    await owner_ready(client)
    await _setup_products(client)
    sample.main(tmp_path)

    batch = await _upload(client, tmp_path / "import-test.xlsx")
    _check(batch, sample.ROWS)
    assert set(batch["summary"]["extra_columns"]) == {"Адрес точки", "Менеджер"}
    by_ext = {(r["product"], r["external_id"]): r for r in batch["rows"] if r["status"] == "ok"}
    ya = sample.YA
    assert by_ext[(ya, "YM-0004")]["units"] == 2
    assert by_ext[(ya, "YM-0005")]["units"] == 3
    assert by_ext[(ya, "YM-0006")]["gender"] == "m"
    assert by_ext[(ya, "YM-0010")]["expected_text"] == sample.DEFAULT_TEXT
    assert by_ext[(ya, "YM-0009")]["external_id"] == "YM-0009"
    assert by_ext[(sample.GIS, "2G-0001")]["type"] == "Отзыв"
    assert len(by_ext[(sample.VK, "VK-0001")]["materials"]) == 2
    assert by_ext[(sample.VK, "VK-0001")]["extra"] == {"Адрес точки": "Мира, 3", "Менеджер": "Анна"}
    assert (sample.VK, "100500") in by_ext

    r = await client.post(f"/api/imports/{batch['id']}/commit", headers=auth(OWNER_TG))
    assert r.status_code == 200, r.text
    assert r.json()["added"] == batch["summary"]["ok"]

    repeat = await _upload(client, tmp_path / "import-test-repeat.xlsx")
    _check(repeat, sample.REPEAT_ROWS)
