"""Распознавание текста на фото (ТЗ 7.1).

Модели PP-OCRv5 (мобильная детекция + славянская модель распознавания), запускаются через
ONNX Runtime (RapidOCR) без фреймворка PaddlePaddle: ~0,4 ГБ памяти в пике и ~1 с на фото
на CPU против ~1,1 ГБ и 5–20 с у PaddleOCR при той же точности на кириллице. Движок
подключается через интерфейс, его можно заменить, не трогая остальной код.
"""

import asyncio
import os
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from app.config import get_settings

# Каталог с моделями, скачанными при сборке образа (scripts/fetch_ocr_models.py).
# Если файлов нет (разработка), RapidOCR скачает модели сам при первом распознавании.
MODELS_DIR = Path(os.environ.get("OCR_MODELS_DIR", "/opt/ocr-models"))
DET_MODEL = "ch_PP-OCRv5_det_mobile.onnx"
REC_MODEL = "eslav_PP-OCRv5_rec_mobile.onnx"
# Длинная сторона фото для поиска текста. 1280 — запас для мелкого текста (проверено на
# шрифте 20 px на фото 1200×1600) при пике памяти ~0,4 ГБ; 960 — ~0,3 ГБ.
DET_LIMIT_SIDE = 1280


class OcrUnavailable(Exception):
    """Движок не установлен или не запускается — проверка уходит людям."""


class OcrEngine(Protocol):
    async def recognize(self, path: Path) -> str: ...


class OnnxEngine:
    def __init__(self) -> None:
        self._ocr = None
        self._lock = threading.Lock()

    def _load(self):
        if self._ocr is None:
            try:
                from rapidocr import RapidOCR
                from rapidocr.utils.typings import LangDet, LangRec, ModelType, OCRVersion
            except ImportError as e:
                raise OcrUnavailable("Модуль OCR не установлен") from e
            params = {
                "Global.use_cls": False,
                "Global.log_level": "warning",
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.MOBILE,
                "Det.lang_type": LangDet.CH,
                "Det.limit_type": "max",
                "Det.limit_side_len": DET_LIMIT_SIDE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.lang_type": LangRec.ESLAV,
            }
            if (MODELS_DIR / DET_MODEL).exists() and (MODELS_DIR / REC_MODEL).exists():
                params["Det.model_path"] = str(MODELS_DIR / DET_MODEL)
                params["Rec.model_path"] = str(MODELS_DIR / REC_MODEL)
            self._ocr = RapidOCR(params=params)
        return self._ocr

    def _run(self, path: Path) -> str:
        with self._lock:  # один распознаватель на процесс: память не растёт с нагрузкой
            result = self._load()(str(path))
            return "\n".join(result.txts or [])

    async def recognize(self, path: Path) -> str:
        return await asyncio.wait_for(asyncio.to_thread(self._run, path), timeout=120)


class DisabledEngine:
    async def recognize(self, path: Path) -> str:
        raise OcrUnavailable("Автоматическая проверка отключена (OCR_ENGINE=none)")


_engine: OcrEngine | None = None


def get_engine() -> OcrEngine:
    global _engine
    if _engine is None:
        # «paddle» — прежнее название движка в .env, работает так же.
        enabled = get_settings().ocr_engine in ("onnx", "paddle")
        _engine = OnnxEngine() if enabled else DisabledEngine()
    return _engine


def set_engine(engine: OcrEngine | None) -> None:
    """Подменить движок (тесты)."""
    global _engine
    _engine = engine


# Латинские буквы, которые OCR путает с кириллическими. Приводятся к кириллице и в
# ожидаемом, и в распознанном тексте, поэтому сравнение остаётся симметричным.
_CONFUSABLE = str.maketrans("aeopcxykmthb", "аеорсхукмтнв")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    t = t.translate(_CONFUSABLE)
    return re.sub(r"[\W_]+", "", t)


def similarity(expected: str, text: str) -> float:
    """Лучшее совпадение ожидаемого текста с фрагментом распознанного, 0..1."""
    e, t = normalize(expected), normalize(text)
    if not e:
        return 0.0
    if e in t:
        return 1.0
    if len(t) <= len(e):
        return SequenceMatcher(None, e, t).ratio()
    best = 0.0
    for i in range(0, len(t) - len(e) + 1):
        r = SequenceMatcher(None, e, t[i : i + len(e)]).ratio()
        if r > best:
            best = r
    return best


def matches(rule: str, expected: str, text: str, threshold: int) -> tuple[bool, float]:
    e, t = normalize(expected), normalize(text)
    if rule == "exact":
        return e == t, 1.0 if e == t else similarity(expected, text)
    if rule == "fuzzy":
        s = similarity(expected, text)
        return s * 100 >= threshold, s
    ok = bool(e) and e in t
    return ok, 1.0 if ok else similarity(expected, text)
