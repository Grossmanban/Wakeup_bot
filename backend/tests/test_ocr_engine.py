"""Настоящий OCR на сгенерированном русском фото.

Запускается, если установлен модуль OCR и скачаны модели (scripts/fetch_ocr_models.py в
каталог OCR_MODELS_DIR), иначе пропускается — сеть в тестах не нужна.
"""

import pytest

from app import ocr

pytest.importorskip("rapidocr")
if not ((ocr.MODELS_DIR / ocr.DET_MODEL).exists() and (ocr.MODELS_DIR / ocr.REC_MODEL).exists()):
    pytest.skip("модели OCR не скачаны", allow_module_level=True)


async def test_recognizes_russian_text(tmp_path):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    img = Image.new("RGB", (1200, 1600), (250, 250, 252))
    font = ImageFont.truetype("DejaVuSans.ttf", 26)
    ImageDraw.Draw(img).text((60, 700), "Спасибо за отзыв! Код заказа: Янд-4821", font=font, fill=0)
    img = img.rotate(-2, fillcolor=(250, 250, 252)).filter(ImageFilter.GaussianBlur(0.8))
    path = tmp_path / "shot.jpg"
    img.save(path, quality=80)

    text = await ocr.OnnxEngine().recognize(path)
    ok, _ = ocr.matches("contains", "Спасибо за отзыв", text, 80)
    assert ok, text
    assert "4821" in text
