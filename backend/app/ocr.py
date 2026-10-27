"""Распознавание текста на фото (ТЗ 7.1).

Движок по умолчанию — PaddleOCR (PP-OCRv5: мобильная детекция + славянская модель
распознавания). Проверено на кириллице: ~1 с на фото на CPU. Движок подключается через
интерфейс, его можно заменить, не трогая остальной код.
"""

import asyncio
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class OcrUnavailable(Exception):
    """Движок не установлен или не запускается — проверка уходит людям."""


class OcrEngine(Protocol):
    async def recognize(self, path: Path) -> str: ...


class PaddleEngine:
    def __init__(self) -> None:
        self._ocr = None
        self._lock = threading.Lock()

    def _load(self):
        if self._ocr is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as e:
                raise OcrUnavailable("PaddleOCR не установлен") from e
            self._ocr = PaddleOCR(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="eslav_PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                # oneDNN в Paddle 3.x падает на этих моделях.
                enable_mkldnn=False,
            )
        return self._ocr

    def _run(self, path: Path) -> str:
        with self._lock:  # модель не потокобезопасна
            ocr = self._load()
            lines: list[str] = []
            for res in ocr.predict(str(path)):
                lines.extend(res["rec_texts"])
            return "\n".join(lines)

    async def recognize(self, path: Path) -> str:
        return await asyncio.wait_for(asyncio.to_thread(self._run, path), timeout=120)


class DisabledEngine:
    async def recognize(self, path: Path) -> str:
        raise OcrUnavailable("Автоматическая проверка отключена (OCR_ENGINE=none)")


_engine: OcrEngine | None = None


def get_engine() -> OcrEngine:
    global _engine
    if _engine is None:
        _engine = PaddleEngine() if get_settings().ocr_engine == "paddle" else DisabledEngine()
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
