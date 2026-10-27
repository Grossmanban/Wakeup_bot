from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения. Полный список — в .env.example."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(repr=False)
    database_url: str = "postgresql+asyncpg://wakeup:wakeup@localhost:5432/wakeup"

    # Telegram ID владельцев: при первом входе получают роль «владелец» без заявки.
    owner_telegram_ids: Annotated[list[int], NoDecode] = []

    # Публичный HTTPS-адрес Mini App (кнопка в боте).
    miniapp_url: str = ""

    # Имя бота без @ — для реферальных ссылок t.me/<бот>?start=ref_<код>.
    bot_username: str = ""

    # Сколько секунд initData считается действительной.
    init_data_max_age: int = 24 * 3600

    # Разрешённые источники для CORS (адрес Mini App при раздельном хостинге).
    cors_origins: Annotated[list[str], NoDecode] = []

    # Каталог для фотографий на диске.
    media_dir: str = "./media"

    # Движок OCR: onnx — модели PP-OCRv5 через ONNX Runtime (app/ocr.py); none —
    # автоматическая проверка отключена, все фото с OCR уходят на ручную проверку.
    ocr_engine: str = "onnx"

    @field_validator("owner_telegram_ids", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
