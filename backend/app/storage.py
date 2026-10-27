"""Хранение фотографий на диске сервера."""

import hashlib
import os
import uuid
from pathlib import Path

from app.config import get_settings
from app.errors import AppError
from app.timeutil import msk_date, now_utc

MAX_PHOTO_BYTES = 15 * 1024 * 1024

_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
)


def detect_image(data: bytes) -> tuple[str, str]:
    for sig, ctype, ext in _SIGNATURES:
        if data.startswith(sig):
            return ctype, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise AppError("bad_photo", "Нужна фотография в формате JPEG, PNG или WebP")


def media_root() -> Path:
    return Path(get_settings().media_dir).resolve()


def save_photo(data: bytes) -> tuple[str, str, str]:
    """Записать файл и дождаться записи на диск. Вернуть (относительный путь, тип, sha256)."""
    if not data:
        raise AppError("bad_photo", "Пустой файл")
    if len(data) > MAX_PHOTO_BYTES:
        raise AppError("bad_photo", "Фото больше 15 МБ")
    ctype, ext = detect_image(data)
    day = msk_date(now_utc())
    rel = Path("photos") / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / f"{uuid.uuid4().hex}.{ext}"
    full = media_root() / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    tmp = full.with_suffix(full.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, full)
    return str(rel), ctype, hashlib.sha256(data).hexdigest()


def photo_path(rel: str) -> Path:
    full = (media_root() / rel).resolve()
    if media_root() not in full.parents:
        raise AppError("bad_path", "Недопустимый путь", 400)
    return full
